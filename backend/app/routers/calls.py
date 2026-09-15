"""Dashboard: call logs (transcript, tool calls, summary)."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Call

router = APIRouter(prefix="/api/calls", tags=["dashboard"])


@router.get("")
async def list_calls(
    disposition: Optional[str] = None,
    call_type: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    q = select(Call).order_by(Call.started_at.desc()).limit(limit)
    if disposition:
        q = q.where(Call.disposition == disposition)
    if call_type:
        q = q.where(Call.call_type == call_type.strip())
    if search:
        like = f"%{search.strip()}%"
        q = q.where(
            Call.summary.ilike(like)
            | Call.customer_name.ilike(like)
            | Call.customer_phone.ilike(like)
            | Call.call_id.ilike(like)
            | Call.first_name.ilike(like)
        )
    rows = (await db.execute(q)).scalars().all()
    return [
        {
            "call_id": c.call_id,
            "call_type": c.call_type or "",
            "disposition": c.disposition,
            "started_at": c.started_at.isoformat(),
            "duration_seconds": c.duration_seconds,
            "customer_name": c.customer_name,
            "customer_phone": c.customer_phone,
            "first_name": c.first_name or "",
            "rsvp": c.rsvp or "",
            "summary": c.summary,
            "escalation_reason": c.escalation_reason,
            "order_numbers": c.order_numbers,
            "sentiment": c.sentiment,
            "recording_url": c.recording_url or "",
            "transcript_url": c.transcript_url or "",
        }
        for c in rows
    ]


@router.get("/{call_id}")
async def call_detail(call_id: str, db: AsyncSession = Depends(get_db)):
    c = (await db.execute(select(Call).where(Call.call_id == call_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Call not found")
    return {
        "call_id": c.call_id,
        "call_type": c.call_type or "",
        "disposition": c.disposition,
        "started_at": c.started_at.isoformat(),
        "ended_at": c.ended_at.isoformat() if c.ended_at else None,
        "duration_seconds": c.duration_seconds,
        "customer_name": c.customer_name,
        "customer_phone": c.customer_phone,
        "first_name": c.first_name or "",
        "rsvp": c.rsvp or "",
        "summary": c.summary,
        "escalation_reason": c.escalation_reason,
        "transcript": c.transcript,
        "tool_calls": c.tool_calls,
        "order_numbers": c.order_numbers,
        "sentiment": c.sentiment,
        "cost": float(c.cost) if c.cost is not None else None,
        "recording_url": c.recording_url,
        "transcript_url": c.transcript_url,
        "user_recording_url": c.user_recording_url,
        "bot_recording_url": c.bot_recording_url,
        "genesys_conversation_id": c.genesys_conversation_id,
        "workflow_id": c.workflow_id,
        "run_id": c.run_id,
        "workflow_name": c.workflow_name,
        "call_type": c.call_type,
    }
