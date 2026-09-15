"""Pluggable embeddings.

Default: deterministic local hashing-trick embedding (no API keys needed, runs
anywhere Docker runs). If GEMINI_API_KEY is set, uses Gemini embeddings for
better recall. Both produce a fixed-dim, L2-normalized vector.
"""

import hashlib
import math
import re

import httpx
from loguru import logger

from app.config import get_settings

settings = get_settings()

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "is", "are", "do", "does", "you", "your", "i", "we",
    "of", "to", "for", "and", "or", "in", "on", "it", "what", "can", "how",
}


def _tokens(text: str) -> list[str]:
    words = [w for w in _WORD_RE.findall(text.lower()) if w not in _STOP]
    # bigrams capture short phrases like "spice level", "family bucket"
    return words + [f"{a}_{b}" for a, b in zip(words, words[1:])]


def _local_embed(text: str) -> list[float]:
    dim = settings.embedding_dim
    vec = [0.0] * dim
    for tok in _tokens(text):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if (h >> 128) & 1 else -1.0
        vec[idx] += sign
        # second hash bucket softens collisions
        idx2 = (h // dim) % dim
        vec[idx2] += sign * 0.5
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


async def _gemini_embed(texts: list[str]) -> list[list[float]] | None:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"text-embedding-004:batchEmbedContents?key={settings.gemini_api_key}"
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url,
                json={
                    "requests": [
                        {
                            "model": "models/text-embedding-004",
                            "content": {"parts": [{"text": t}]},
                        }
                        for t in texts
                    ]
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return [e["values"] for e in data["embeddings"]]
    except Exception as e:
        logger.warning(f"Gemini embedding failed, falling back to local: {e}")
        return None


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if settings.gemini_api_key and texts:
        result = await _gemini_embed(texts)
        if result is not None:
            return result
    return [_local_embed(t) for t in texts]


async def embed_one(text: str) -> list[float]:
    return (await embed_texts([text]))[0]
