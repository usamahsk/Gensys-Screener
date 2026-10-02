"""Post-call summarization.

With GEMINI_API_KEY: LLM summary of the transcript.
Without: extractive fallback (intent from first customer turn + actions taken
+ outcome) so call summaries always exist, even fully offline.
"""

import httpx
from loguru import logger

from app.config import get_settings
from app.services.gemini import redact as _redact_key

settings = get_settings()

_PROMPT = """Summarize this voice call in 3-4 sentences for a restaurant staff dashboard.
Cover: what the customer wanted, what was done (order placed / status checked / questions answered),
and the outcome or reason for transfer if any. Be factual, use only the transcript.

Transcript:
{transcript}
"""


async def summarize(transcript: list[dict], tool_calls: list[dict], disposition: str,
                    purpose: str = "") -> str:
    lines = "\n".join(
        f"{t.get('role', 'unknown')}: {t.get('content', '')}" for t in transcript
    ) or "(no transcript)"
    if tool_calls:
        lines += "\n\nTools used: " + ", ".join(
            f"{tc.get('name', '?')}({tc.get('args_summary', '')})" for tc in tool_calls
        )

    if settings.gemini_api_key:
        from app.services.gemini import generate_content
        try:
            return await generate_content(
                settings.gemini_api_key, settings.gemini_model,
                [{"parts": [{"text": _PROMPT.format(transcript=lines)}]}],
                timeout=20,
            )
        except Exception as e:
            logger.warning(
                f"Gemini summary failed, using extractive fallback: {_redact_key(str(e))}")

    return _extractive(transcript, tool_calls, disposition, purpose)


_CUSTOMER_ROLES = {"customer", "user", "caller", "human", "client", "caller-side", "end-user", "enduser"}
_AGENT_ROLES = {"assistant", "agent", "bot", "ai", "system"}


def _turn_text(t: object) -> str:
    if isinstance(t, dict):
        c = t.get("content") or t.get("text") or ""
        return str(c).strip()
    return str(t or "").strip()


def _first_customer_turn(transcript: list[dict]) -> str:
    """First caller-side turn, tolerant of role spellings and blob transcripts.

    Handles: role aliases (user/caller/human...), case differences, a single
    {"role": "transcript", "content": "<full text>"} blob (picks the first
    customer:/user:/caller: line), and dicts using `text` instead of content.
    """
    if not transcript:
        return ""
    # 1) direct caller-side role match (case-insensitive)
    for t in transcript:
        if not isinstance(t, dict):
            continue
        if str(t.get("role", "")).strip().lower() in _CUSTOMER_ROLES:
            c = _turn_text(t)
            if c:
                return c
    # 2) single-blob transcript: scan lines for a caller prefix
    blobs = [_turn_text(t) for t in transcript if isinstance(t, dict)]
    for blob in blobs:
        if "\n" in blob and any(
            k in blob.lower() for k in ("customer:", "user:", "caller:", "human:")
        ):
            for line in blob.splitlines():
                s = line.strip()
                low = s.lower()
                for prefix in ("customer:", "user:", "caller:", "human:", "client:"):
                    if low.startswith(prefix):
                        body = s[len(prefix):].strip()
                        if body:
                            return body
            break
    # 3) first substantive non-agent turn (covers unknown role spellings)
    for t in transcript:
        if not isinstance(t, dict):
            continue
        if str(t.get("role", "")).strip().lower() not in _AGENT_ROLES:
            c = _turn_text(t)
            if c and c != "(no transcript)":
                return c
    # 4) anything at all (even agent line beats "not captured")
    for t in transcript:
        c = _turn_text(t)
        if c and c != "(no transcript)":
            return c
    return ""


def _clean_snippet(s: str, max_len: int = 180) -> str:
    """One-line snippet without transcript timestamp markers."""
    import re as _re

    one_line = _re.sub(r"\[\d{4}-\d{2}-\d{2}T[^\]]*\]\s*", "", s or "")
    one_line = " ".join(one_line.split()).strip()
    if len(one_line) > max_len:
        return one_line[: max_len - 1].rstrip() + "…"
    return one_line


def _last_turn(transcript: list[dict], customer_side: bool) -> str:
    """Last caller-side (or agent-side) turn text, timestamp markers stripped."""
    roles = _CUSTOMER_ROLES if customer_side else _AGENT_ROLES
    for t in reversed(transcript or []):
        if not isinstance(t, dict):
            continue
        if str(t.get("role", "")).strip().lower() in roles:
            c = _clean_snippet(_turn_text(t))
            if c and c != "(no transcript)":
                return c
    return ""


def _intent_from_tools(tool_calls: list[dict]) -> str:
    """Intent hint from tool activity when the transcript has no caller turn."""
    names = " ".join(str(tc.get("name", "")) for tc in (tool_calls or [])).lower()
    summaries = " ".join(str(tc.get("args_summary", "")) for tc in (tool_calls or [])).strip()
    order_ref = ""
    for tc in tool_calls or []:
        s = str(tc.get("args_summary", "") or "")
        if s.strip():
            order_ref = s.strip()[:80]
            break
    if any(k in names for k in ("reserv", "book", "table")):
        return "book a table" + (f" ({order_ref})" if order_ref else "")
    if any(k in names for k in ("status", "track", "lookup")):
        return "check order status" + (f" ({order_ref})" if order_ref else "")
    if any(k in names for k in ("order", "create")):
        return "place a food order" + (f" ({order_ref})" if order_ref else "")
    return ""


_PURPOSE_INTENT = {
    "order_creation": "place a food order",
    "order_status": "check order status",
    "reservation": "book a table",
}


def _extractive(transcript: list[dict], tool_calls: list[dict], disposition: str,
                purpose: str = "") -> str:
    """Offline 3–4 sentence continue-brief (no LLM): want → did → stands → next.

    Never returns a bare "Call completed successfully." — even a 2-turn call
    yields what the customer said, what the agent said/did, and where to
    continue, so Agent View always has something to brief on.
    """
    first_customer = _clean_snippet(_first_customer_turn(transcript))
    intent = first_customer
    if not intent:
        intent = _intent_from_tools(tool_calls)
    if not intent and (purpose or "").strip().lower() in _PURPOSE_INTENT:
        intent = _PURPOSE_INTENT[(purpose or "").strip().lower()]
    actions = [tc.get("name", "?") for tc in (tool_calls or []) if isinstance(tc, dict)]
    last_customer = _last_turn(transcript, customer_side=True)
    last_agent = _last_turn(transcript, customer_side=False)

    sentences: list[str] = []
    if intent:
        sentences.append(f'The customer called to {intent.rstrip(".")}.'
                         if not intent.startswith("The customer")
                         and len(intent.split()) < 12
                         else f'Customer said: "{intent}".')
    elif last_customer:
        sentences.append(f'Customer said: "{last_customer}".')
    if actions:
        sentences.append(f"The voice agent already ran: {', '.join(actions)}.")
    elif last_agent and last_agent != (sentences[0] if sentences else None):
        sentences.append(f'The agent replied: "{last_agent}".')
    if last_customer and last_customer not in (intent or ""):
        sentences.append(f'Latest from the customer: "{last_customer}".')
    elif last_agent and len(sentences) < 3:
        pass  # agent line already captured above
    outcome = {
        "transferred": "The call was handed to a human agent — continue from the last message.",
        "failed": "The call ended in error — check the transcript tail before calling back.",
        "abandoned": "The customer hung up — follow up from the last message.",
    }.get((disposition or "").strip().lower(),
          "Continue from the last message so the caller does not repeat themselves.")
    sentences.append(outcome)
    brief = " ".join(s for s in sentences if s).strip()
    return brief[:900] or "Continue from the transcript below."
