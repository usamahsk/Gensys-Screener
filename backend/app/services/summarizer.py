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
    first_customer = _first_customer_turn(transcript)
    intent = first_customer[:160] + ("…" if len(first_customer) > 160 else "")
    if not intent:
        intent = _intent_from_tools(tool_calls)
    if not intent and (purpose or "").strip().lower() in _PURPOSE_INTENT:
        intent = _PURPOSE_INTENT[(purpose or "").strip().lower()]
    actions = [tc.get("name", "?") for tc in tool_calls]
    action_part = f" Actions taken: {', '.join(actions)}." if actions else ""
    outcome = {
        "transferred": "Call was transferred to a human agent.",
        "failed": "Call ended in error.",
        "abandoned": "Customer hung up before completion.",
    }.get(disposition, "Call completed successfully.")
    return f"Customer intent: {intent or 'not captured'}.{action_part} {outcome}"
