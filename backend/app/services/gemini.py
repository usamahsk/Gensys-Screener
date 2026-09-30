"""Shared Google Gemini REST helper (per current ai.google.dev docs).

Two things break over time and both are handled here:

- Auth goes in the ``x-goog-api-key`` header (the ``?key=`` query param
  is legacy).
- Model names retire (the whole 2.x generation now 404s). The configured
  name is resolved against ``GET /v1beta/models`` at runtime (cached 1h
  per key) — if it no longer exists, the first ``generateContent``-capable
  model is used instead of failing the call.

API keys are redacted from every log line this module emits.
"""

import re
import time

import httpx
from loguru import logger

BASE = "https://generativelanguage.googleapis.com/v1beta"

# Sensible preferences when the live model list can't be reached.
PREFERRED_FALLBACKS = ("gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.0-flash")

_cache: dict[str, tuple[float, list[str]]] = {}
_TTL_SECONDS = 3600


def redact(msg: str) -> str:
    msg = re.sub(r"key=[^'\"\s&]+", "key=***", msg)
    return re.sub(r"(x-goog-api-key['\"]?\s*:\s*)[^\s'\",}]+", r"\1***", msg, flags=re.I)


def _short(name: str) -> str:
    return str(name).split("/")[-1]


async def list_model_names(api_key: str) -> list[str]:
    """Short names of models supporting generateContent for this key."""
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{BASE}/models", headers={"x-goog-api-key": api_key})
        r.raise_for_status()
        body = r.json()
    names: list[str] = []
    for m in body.get("models", []) or []:
        if not isinstance(m, dict):
            continue
        if "generateContent" in (m.get("supportedGenerationMethods") or []) and m.get("name"):
            names.append(_short(m["name"]))
    return names


async def resolve_model(api_key: str, preferred: str) -> str:
    """Configured name when it exists, else a usable model. Never raises."""
    want = (preferred or "").strip()
    now = time.time()
    hit = _cache.get(api_key)
    if hit is not None and now - hit[0] < _TTL_SECONDS:
        names = hit[1]
    else:
        try:
            names = await list_model_names(api_key)
            _cache[api_key] = (now, names)
        except Exception as e:
            logger.warning(
                f"gemini: model list unreachable, keeping {want!r}: {redact(str(e))}")
            return want or PREFERRED_FALLBACKS[0]
    for cand in [want, *PREFERRED_FALLBACKS, *names]:
        if cand and cand in names:
            if cand != want and want:
                logger.info(f"gemini: model {want!r} unavailable, using {cand!r}")
            return cand
    return want or PREFERRED_FALLBACKS[0]


async def generate_content(
    api_key: str,
    model: str,
    contents: list[dict],
    generation_config: dict | None = None,
    timeout: int = 60,
) -> str:
    """POST generateContent with header auth + resolved model. Returns text."""
    m = await resolve_model(api_key, model)
    body: dict = {"contents": contents}
    if generation_config:
        body["generationConfig"] = generation_config
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            f"{BASE}/models/{m}:generateContent",
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=body,
        )
        r.raise_for_status()
        data = r.json()
    parts = data["candidates"][0]["content"]["parts"]
    return " ".join(p.get("text", "") for p in parts).strip()
