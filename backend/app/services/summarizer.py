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


async def summarize(transcript: list[dict], tool_calls: list[dict], disposition: str) -> str:
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

    return _extractive(transcript, tool_calls, disposition)


def _extractive(transcript: list[dict], tool_calls: list[dict], disposition: str) -> str:
    first_customer = next(
        (t["content"] for t in transcript if t.get("role") == "customer"), ""
    )
    intent = first_customer[:160] + ("…" if len(first_customer) > 160 else "")
    actions = [tc.get("name", "?") for tc in tool_calls]
    action_part = f" Actions taken: {', '.join(actions)}." if actions else ""
    outcome = {
        "transferred": "Call was transferred to a human agent.",
        "failed": "Call ended in error.",
        "abandoned": "Customer hung up before completion.",
    }.get(disposition, "Call completed successfully.")
    return f"Customer intent: {intent or 'not captured'}.{action_part} {outcome}"
