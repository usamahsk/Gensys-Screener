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
    purpose: Optional[str] = None,
    search: Optional[str] = None,
    has_provider_id: bool = False,
    has_run: bool = False,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List call logs. `purpose` filters by call intent: order_creation |
    order_status | reservation | general (order-status / reservation calls
    create no order row — filter them here). `has_provider_id` keeps only
    rows with a provider/telephone id or Genesys conversation id;
    `has_run` keeps only rows linked to a Dograh run id."""
    from app.models import Customer as _Customer

    q = select(Call).order_by(Call.started_at.desc()).limit(limit)
    if disposition:
        q = q.where(Call.disposition == disposition)
    if call_type:
        q = q.where(Call.call_type == call_type.strip())
    if purpose:
        q = q.where(Call.call_purpose == purpose.strip())
    if search:
        like = f"%{search.strip()}%"
        q = q.where(
            Call.summary.ilike(like)
            | Call.customer_name.ilike(like)
            | Call.customer_phone.ilike(like)
            | Call.call_id.ilike(like)
            | Call.first_name.ilike(like)
            | Call.provider_call_id.ilike(like)
            | Call.provider.ilike(like)
            | Call.genesys_conversation_id.ilike(like)
        )
    rows = (await db.execute(q)).scalars().all()
    if has_run:
        rows = [c for c in rows if c.run_id]
    if has_provider_id:
        rows = [c for c in rows if (c.provider_call_id or "").strip()
                or (c.genesys_conversation_id or "").strip()]
    # Attach captured customer intent (Call row first, Customer memory fallback).
    phones = {(c.customer_phone or "").strip() for c in rows if (c.customer_phone or "").strip()}
    intent_by_phone: dict[str, dict] = {}
    if phones:
        for cust in (await db.execute(
                select(_Customer).where(_Customer.phone.in_(phones)))).scalars().all():
            intent_by_phone[cust.phone] = {
                "last_intent": cust.last_intent or "",
                "last_order_number": cust.last_order_number or "",
            }
    return [
        {
            "call_id": c.call_id,
            "call_type": c.call_type or "",
            "call_purpose": c.call_purpose or "",
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
            "provider": c.provider or "dograh",
            "provider_call_id": c.provider_call_id or "",
            "genesys_conversation_id": c.genesys_conversation_id or "",
            "run_id": c.run_id,
            "workflow_id": c.workflow_id,
            "workflow_name": c.workflow_name or "",
            "customer_intent": (c.customer_intent or c.call_purpose or "") or "",
            "customer_last_intent": intent_by_phone.get((c.customer_phone or "").strip(), {}).get("last_intent", ""),
            "customer_last_order": intent_by_phone.get((c.customer_phone or "").strip(), {}).get("last_order_number", ""),
            "initial_context": c.initial_context or {},
            "gathered_context": c.gathered_context or {},
        }
        for c in rows
    ]


@router.post("/repair-dispositions", response_model=dict)
async def repair_dispositions(db: AsyncSession = Depends(get_db)):
    """Repair blanket-transfer damage: `transferred` rows with no reason.

    - Real handoffs (transfer evidence in tools/transcript) stay transferred
      and get the inferred reason filled in.
    - Rows with no transfer evidence at all go back to `completed`.
    Returns {scanned, kept_transferred, fixed_to_completed}.
    """
    from app.routers.webhooks import _infer_transfer_reason

    rows = (await db.execute(
        select(Call).where(Call.disposition == "transferred"))).scalars().all()
    kept, fixed = 0, 0
    for c in rows:
        if (c.escalation_reason or "").strip():
            kept += 1
            continue
        reason = _infer_transfer_reason(c.transcript, c.tool_calls,
                                        c.gathered_context, c.initial_context)
        if reason:
            c.escalation_reason = reason[:512]
            kept += 1
        else:
            c.disposition = "completed"
            fixed += 1
    await db.commit()
    return {"scanned": len(rows), "kept_transferred": kept,
            "fixed_to_completed": fixed}


@router.get("/{call_id}")
async def call_detail(call_id: str, db: AsyncSession = Depends(get_db)):
    from app.models import Customer as _Customer

    c = (await db.execute(select(Call).where(Call.call_id == call_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Call not found")
    cust_intent, cust_last_order, cust_context = "", "", {}
    if (c.customer_phone or "").strip():
        cust = (await db.execute(
            select(_Customer).where(_Customer.phone == c.customer_phone.strip())
        )).scalar_one_or_none()
        if cust:
            cust_intent = cust.last_intent or ""
            cust_last_order = cust.last_order_number or ""
            cust_context = cust.context or {}
    return {
        "call_id": c.call_id,
        "call_type": c.call_type or "",
        "call_purpose": c.call_purpose or "",
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
        "provider": c.provider or "dograh",
        "provider_call_id": c.provider_call_id or "",
        "workflow_id": c.workflow_id,
        "run_id": c.run_id,
        "workflow_name": c.workflow_name,
        "transcript_turns": len(c.transcript or []),
        "customer_intent": (c.customer_intent or c.call_purpose or "") or "",
        "customer_last_intent": cust_intent,
        "customer_last_order": cust_last_order,
        "customer_context": cust_context,
        "initial_context": c.initial_context or {},
        "gathered_context": c.gathered_context or {},
    }
