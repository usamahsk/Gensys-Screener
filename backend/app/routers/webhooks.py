"""Webhooks from Dograh: post-call logging and escalation handoff.

Transfer flow: when the voice agent escalates, Dograh calls /transfer; we
record the reason + build the agent context payload. If Genesys OAuth creds
are configured on the Dograh side, the same context is pushed to the Genesys
conversation as participant attributes (handled in dograh-claude's
genesys provider session_logger) — this endpoint is the app-side record for
the dashboard's agent view.
"""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.models import Call, Customer, Memory, Order, OrderEvent, OrderItem
from app.schemas import CallEndedWebhook, TransferContextResponse, TransferWebhook
from app.services.embeddings import embed_one
from app.services.summarizer import summarize

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/call-ended")
async def call_ended(
    payload: CallEndedWebhook, request: Request, background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    logger.info(
        f"POST /api/webhooks/call-ended from {request.client.host if request.client else '?'} "
        f"| call_id={payload.resolved_call_id()!r} customer={payload.resolved_customer_name()!r} "
        f"phone={payload.resolved_customer_phone()!r} order={payload.order_number!r} "
        f"items={str(payload.order_items)[:300]!r} total={payload.total_price!r} "
        f"fulfillment={payload.fulfillment_type!r} disposition={payload.resolved_disposition()!r}"
    )
    call_id = payload.resolved_call_id() or "unknown"
    existing = (
        await db.execute(select(Call).where(Call.call_id == call_id))
    ).scalar_one_or_none()
    if existing:
        return {"status": "duplicate", "call_id": call_id}

    disposition = payload.resolved_disposition()
    duration = payload.resolved_duration()
    customer_name = payload.resolved_customer_name()
    customer_phone = payload.resolved_customer_phone()

    summary = await summarize(payload.transcript, payload.tool_calls, disposition)
    if not summary and (payload.order_number or payload.transfer_reason):
        summary = _synthesize_summary(payload)

    order_numbers = list(payload.order_numbers or [])
    if payload.order_number and payload.order_number not in order_numbers:
        order_numbers.append(payload.order_number)

    call = Call(
        call_id=call_id,
        disposition=disposition,
        started_at=datetime.now(timezone.utc) - timedelta(seconds=duration),
        ended_at=datetime.now(timezone.utc),
        duration_seconds=duration,
        customer_name=customer_name,
        customer_phone=customer_phone,
        first_name=payload.first_name or "",
        rsvp=payload.rsvp or "",
        transcript=payload.transcript,
        tool_calls=payload.tool_calls,
        order_numbers=order_numbers,
        summary=summary,
        escalation_reason=payload.transfer_reason or "",
        sentiment=payload.sentiment or "",
        cost=payload.cost if payload.cost is not None else (
            round(duration * 0.0032, 4) if duration else None
        ),
        recording_url=payload.resolved_recording_url(),
        transcript_url=payload.transcript_url or "",
        user_recording_url=payload.user_recording_url or "",
        bot_recording_url=payload.bot_recording_url or "",
        genesys_conversation_id=payload.genesys_conversation_id or "",
    )
    db.add(call)

    if customer_phone:
        cust = (
            await db.execute(select(Customer).where(Customer.phone == customer_phone))
        ).scalar_one_or_none()
        if not cust:
            cust = Customer(phone=customer_phone, name=customer_name)
            db.add(cust)
            await db.flush()
        if customer_name:
            cust.name = cust.name or customer_name
        if payload.email:
            cust.email = cust.email or payload.email
        vec = await embed_one(f"Call summary: {summary}")
        db.add(Memory(customer_id=cust.id, kind="call_summary", text=summary, embedding=vec))

    order = await _materialize_order(db, payload, customer_name, customer_phone, call_id)
    if order is None and call_id and call_id != "unknown":
        # STRAIGHT PATH: transcript arrives in this request body → extract via
        # Gemini NOW and create the order. Nothing is saved on failure — the
        # UI only ever shows real, extracted orders.
        try:
            from app.services.order_recovery import (
                _apply_extraction,
                _download_text,
                _transcript_text,
                create_order_from_transcript,
                extract_request_from_text,
                get_google_key,
            )
            existing = (
                await db.execute(
                    select(Order)
                    .options(selectinload(Order.items), selectinload(Order.events))
                    .where(Order.call_id == call_id)
                    .order_by(Order.id.desc()).limit(1)
                )
            ).scalars().first()
            text = _transcript_text(payload)
            if not text and payload.transcript_url:
                text = await _download_text(payload.transcript_url)
            api_key, model, enabled = await get_google_key(db)
            req = (
                await extract_request_from_text(text, api_key, model)
                if (enabled and api_key and text.strip()) else None
            )
            if req is not None:
                if existing:
                    _apply_extraction(existing, req, call, shell_empty=not existing.items)
                    order = existing
                else:
                    order = await create_order_from_transcript(
                        db, text, call, customer_name, customer_phone, call_id)
        except Exception as e:
            logger.warning(f"call {call_id!r}: extraction failed ({e})")
            order = None

    await db.commit()
    # Background pass only when no order exists yet — it creates the order on
    # success and saves nothing on failure.
    if order is None and call_id and call_id != "unknown":
        from app.services.order_recovery import recover_order_from_call
        background.add_task(recover_order_from_call, call_id, None)
    return {
        "status": "recorded",
        "call_id": call_id,
        "disposition": disposition,
        "summary": summary,
        "order_number": order.order_number if order else None,
    }


async def _materialize_order(
    db: AsyncSession, p: CallEndedWebhook, customer_name: str, customer_phone: str, call_id: str
) -> Order | None:
    """Create/update an order row when the post-call payload carries one."""
    if not p.order_number:
        return None
    order = (
        await db.execute(select(Order).where(Order.order_number == p.order_number))
    ).scalar_one_or_none()
    order_type = "takeaway"
    ft = (p.fulfillment_type or "").lower()
    if ft in ("dinein", "dine_in", "dine-in"):
        order_type = "dinein"
    if order:
        order.status = "confirmed" if p.order_completed or p.order_confirmed else order.status
        order.total = Decimal(str(p.total_price)) if p.total_price else order.total
        if not order.call_id and call_id and call_id != "unknown":
            order.call_id = call_id
        if p.delivery_address and not order.delivery_address:
            order.delivery_address = p.delivery_address
        if p.payment_method and not order.payment_method:
            order.payment_method = p.payment_method
        return order
    order = Order(
        order_number=p.order_number,
        customer_name=customer_name,
        customer_phone=customer_phone,
        email=p.email or "",
        order_type=order_type,
        pickup_time="",
        delivery_address=p.delivery_address or "",
        payment_method=p.payment_method or "",
        status="confirmed" if p.order_completed or p.order_confirmed else "confirmed",
        total=Decimal(str(p.total_price)) if p.total_price else Decimal("0"),
        source="voice_agent",
        call_id=call_id,
    )
    db.add(order)
    await db.flush()
    for item in _parse_items(p.order_items):
        try:
            qty = int(float(str(item.get("quantity", 1))))
        except (ValueError, TypeError):
            qty = 1
        try:
            price = Decimal(str(item.get("price", item.get("unit_price", 0)) or 0))
        except (ValueError, TypeError, ArithmeticError):
            price = Decimal("0")
        db.add(OrderItem(
            order_id=order.id, sku=str(item.get("sku", ""))[:128],
            name=str(item.get("name", "Item"))[:128],
            quantity=max(1, min(qty, 20)),
            unit_price=price,
            size=str(item.get("size", ""))[:32], notes=str(item.get("notes", ""))[:256],
        ))
    db.add(OrderEvent(order_id=order.id, status="confirmed",
                      note="Order recorded from post-call summary"))
    return order


def _parse_items(order_items: Any) -> list[dict]:
    if isinstance(order_items, list):
        return [i for i in order_items if isinstance(i, dict)]
    if isinstance(order_items, str) and order_items.strip():
        try:
            data = json.loads(order_items)
            if isinstance(data, list):
                return [i for i in data if isinstance(i, dict)]
        except (json.JSONDecodeError, ValueError):
            pass
    return []


def _synthesize_summary(p: CallEndedWebhook) -> str:
    parts = []
    if p.customer_name or p.first_name:
        parts.append(f"Customer: {p.resolved_customer_name()}.")
    if p.order_number:
        parts.append(
            f"Order {p.order_number} ({p.fulfillment_type or 'takeaway'}) for ${p.total_price or 0}."
        )
    if p.transfer_reason:
        parts.append(f"Transferred to agent: {p.transfer_reason}.")
    if p.call_outcome:
        parts.append(f"Outcome: {p.call_outcome}.")
    return " ".join(parts) or "Call recorded from post-call summary."


@router.post("/transfer", response_model=TransferContextResponse)
async def transfer(payload: TransferWebhook, db: AsyncSession = Depends(get_db)) -> TransferContextResponse:
    call = (
        await db.execute(select(Call).where(Call.call_id == payload.call_id))
    ).scalar_one_or_none()

    agent_context: dict[str, Any] = {"reason_for_transfer": payload.reason}
    if call:
        call.disposition = "transferred"
        call.escalation_reason = payload.reason
        agent_context["conversation"] = {
            "ai_summary": call.summary,
            "transcript_turns": len(call.transcript),
            "orders_touched": call.order_numbers,
        }
        if payload.customer_name:
            call.customer_name = call.customer_name or payload.customer_name
        if payload.customer_phone:
            call.customer_phone = call.customer_phone or payload.customer_phone
    if payload.customer_phone:
        agent_context["customer"] = {"phone": payload.customer_phone,
                                     "name": payload.customer_name or ""}
        last_order = (
            await db.execute(
                select(Order)
                .where(Order.customer_phone == payload.customer_phone)
                .order_by(Order.created_at.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if last_order:
            agent_context["order"] = {
                "order_number": last_order.order_number,
                "status": last_order.status,
                "total": float(last_order.total),
            }

    await db.commit()
    return TransferContextResponse(call_id=payload.call_id, recorded=call is not None,
                                   agent_context=agent_context)
