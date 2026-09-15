"""RAG retrieval over the FAQ knowledge base.

Grounded-answer policy: return matched FAQ content + a confidence score.
When confidence is below threshold the caller (voice agent) should fall back /
escalate instead of answering — that decision is made by the agent prompt using
the `can_answer` flag we return.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Faq
from app.services.embeddings import embed_one

CONFIDENCE_THRESHOLD = 0.45


def _cosine_confidence(stored, vec: list[float]) -> float:
    """pgvector returns the column value as a numpy array — compute cosine in numpy."""
    import math

    if stored is None:
        return 0.0
    a = list(vec)
    b = list(stored)
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    sim = dot / (na * nb)
    return round(max(0.0, min(1.0, sim)), 3)


@dataclass
class RagResult:
    answer: str
    question: str
    confidence: float
    can_answer: bool


async def query_faq(db: AsyncSession, question: str, top_k: int = 3) -> RagResult:
    vec = await embed_one(question)
    rows = (
        (await db.execute(
            select(Faq)
            .where(Faq.active.is_(True))
            .order_by(Faq.embedding.cosine_distance(vec))
            .limit(top_k)
        ))
        .scalars()
        .all()
    )
    if not rows:
        return RagResult("", "", 0.0, False)

    best = rows[0]
    confidence = _cosine_confidence(best.embedding, vec)
    context = "\n".join(f"Q: {r.question}\nA: {r.answer}" for r in rows[:2])
    return RagResult(
        answer=context if confidence < 0.8 else best.answer,
        question=best.question,
        confidence=round(confidence, 3),
        can_answer=confidence >= CONFIDENCE_THRESHOLD,
    )
