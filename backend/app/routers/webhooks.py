"""Webhooks from the voice platform: post-call logging and escalation handoff.

Transfer flow: when the voice agent escalates, the voice platform calls /transfer; we
record the reason + build the agent context payload. If Genesys OAuth creds
are configured on the voice platform side, the same context is pushed to the Genesys
conversation as participant attributes (handled in dograh-claude's
genesys provider session_logger) — this endpoint is the app-side record for
the dashboard's agent view.
"""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
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
        # Retry / late-arriving end-of-call: never lose a Dograh-side
        # transfer. A mid-call /transfer creates a placeholder row first;
        # the final call-ended (disposition=transfer) then fills in the
        # transcript + summary. Retried call-ended webhooks also upgrade
        # a completed row to transferred when the transfer info arrives.
        updated_fields: list[str] = []
        incoming_disposition = payload.resolved_disposition()
        if incoming_disposition == "transferred" and existing.disposition != "transferred":
            existing.disposition = "transferred"
            updated_fields.append("disposition")
        incoming_reason = (payload.transfer_reason or "").strip()
        if incoming_reason and not (existing.escalation_reason or "").strip():
            existing.escalation_reason = incoming_reason
            updated_fields.append("escalation_reason")
        if payload.transcript and not (existing.transcript or []):
            existing.transcript = payload.transcript
            updated_fields.append("transcript")
        incoming_purpose = payload.resolved_purpose(_payload_text(payload))
        if incoming_purpose and (existing.call_purpose or "").strip() in ("", "general"):
            existing.call_purpose = incoming_purpose
            updated_fields.append("call_purpose")
        if payload.tool_calls and not (existing.tool_calls or []):
            existing.tool_calls = payload.tool_calls
            updated_fields.append("tool_calls")
        if not (existing.summary or "").strip():
            new_summary = await summarize(
                payload.transcript, payload.tool_calls, incoming_disposition,
                existing.call_purpose or incoming_purpose)
            if not new_summary and (payload.order_number or payload.transfer_reason):
                new_summary = _synthesize_summary(payload)
            if new_summary:
                existing.summary = new_summary
                updated_fields.append("summary")
        if payload.order_number and payload.order_number not in (existing.order_numbers or []):
            existing.order_numbers = [*(existing.order_numbers or []), payload.order_number]
            updated_fields.append("order_numbers")
        if not (existing.customer_phone or "").strip() and payload.resolved_customer_phone():
            existing.customer_phone = payload.resolved_customer_phone()
            updated_fields.append("customer_phone")
        if not (existing.customer_name or "").strip() and payload.resolved_customer_name():
            existing.customer_name = payload.resolved_customer_name()
            updated_fields.append("customer_name")
        if not (existing.first_name or "").strip() and payload.resolved_first_name():
            existing.first_name = payload.resolved_first_name()
            updated_fields.append("first_name")
        if payload.initial_context and not (existing.initial_context or {}):
            existing.initial_context = payload.initial_context
            updated_fields.append("initial_context")
        if payload.gathered_context and not (existing.gathered_context or {}):
            existing.gathered_context = payload.gathered_context
            updated_fields.append("gathered_context")
        if incoming_purpose and not (existing.customer_intent or "").strip():
            existing.customer_intent = incoming_purpose[:64]
            updated_fields.append("customer_intent")
        if updated_fields:
            existing.ended_at = existing.ended_at or datetime.now(timezone.utc)
            await db.commit()
            return {"status": "updated", "call_id": call_id,
                    "updated": updated_fields, "disposition": existing.disposition}
        return {"status": "duplicate", "call_id": call_id}

    disposition = payload.resolved_disposition()
    duration = payload.resolved_duration()
    customer_name = payload.resolved_customer_name()
    customer_phone = payload.resolved_customer_phone()
    provider = payload.resolved_provider()
    provider_call_id = payload.resolved_provider_call_id()
    # Genesys id stays the canonical Genesys-flavoured column; mirror the
    # generic telephony id into it when the provider is Genesys (or when the
    # payload only carried the legacy genesys_conversation_id field).
    genesys_id = payload.genesys_conversation_id or (
        provider_call_id if provider == "genesys" else ""
    )
    if provider == "genesys" and not provider_call_id:
        provider_call_id = genesys_id

    _purpose_text = _payload_text(payload)
    _webhook_purpose = payload.resolved_purpose(_purpose_text)

    summary = await summarize(
        payload.transcript, payload.tool_calls, disposition,
        _webhook_purpose)
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
        first_name=payload.resolved_first_name(),
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
        genesys_conversation_id=genesys_id,
        provider=provider,
        provider_call_id=provider_call_id,
        call_purpose=_webhook_purpose,
        initial_context=payload.initial_context or {},
        gathered_context=payload.gathered_context or {},
        customer_intent=(_webhook_purpose or "")[:64],
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
        email = payload.resolved_email()
        if email:
            cust.email = cust.email or email
        _purpose_to_intent = {
            "order_creation": "order_placement",
            "order_status": "order_status_check",
            "reservation": "reservation",
            "general": "general_inquiry",
        }
        _p = (_webhook_purpose or "").strip()
        if _p in _purpose_to_intent:
            cust.last_intent = _purpose_to_intent[_p]
        vec = await embed_one(f"Call summary: {summary}")
        db.add(Memory(customer_id=cust.id, kind="call_summary", text=summary, embedding=vec))

    order = await _materialize_order(db, payload, customer_name, customer_phone, call_id)
    if order is None and call_id and call_id != "unknown":
        # STRAIGHT PATH: transcript arrives in this request body → classify
        # via Gemini NOW. order_status / reservation / general only tags the
        # call purpose (no row); order_creation always creates a row —
        # `confirmed` when complete, `incomplete` when fields are missing.
        try:
            from app.services.order_recovery import (
                _apply_extraction,
                _download_text,
                _transcript_text,
                analyze_transcript,
                build_order_from_req,
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
            result = (
                await analyze_transcript(text, api_key, model)
                if (enabled and api_key and text.strip()) else None
            )
            if result is not None:
                if (call.call_purpose or "").strip() in ("", "general"):
                    call.call_purpose = result["purpose"] or _webhook_purpose
                req = result["req"]
                if req is not None:
                    if existing:
                        _apply_extraction(existing, req, call, shell_empty=not existing.items)
                        order = existing
                    else:
                        order = await build_order_from_req(
                            db, req, list(result["missing"] or []), text,
                            call, customer_name, customer_phone, call_id)
            elif not (call.call_purpose or "").strip() and _webhook_purpose:
                # No LLM result: keep the flag/keyword purpose; never invent
                # "general" — unknown stays empty until the classifier runs.
                call.call_purpose = _webhook_purpose
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
    """Create/update an order row when the post-call payload carries one.

    Segregation guard: reservation calls and pure order_status checks never
    mint an order row here. A status check only touches the row when it
    already exists (link/call_id backfill); otherwise it returns None and the
    transcript classifier tags the Call purpose instead. Real creations (or
    the classifier's incomplete path) still create the row.
    """
    if not p.order_number:
        return None
    order = (
        await db.execute(select(Order).where(Order.order_number == p.order_number))
    ).scalar_one_or_none()
    purpose = p.resolved_purpose(_payload_text(p))
    if purpose == "reservation":
        return None
    if order is None:
        if purpose == "order_status":
            return None
        has_items = bool(_parse_items(p.order_items))
        if not (p.order_completed or p.order_confirmed or has_items or p.total_price):
            # Bare order_number echo with no creation evidence (typical of a
            # status-check webhook) — do NOT mint a row; the transcript
            # classifier decides (creation → confirmed/incomplete, status →
            # tag-only).
            return None
    order_type = "takeaway"
    ft = (p.fulfillment_type or "").lower()
    if ft in ("dinein", "dine_in", "dine-in"):
        order_type = "dinein"
    if order:
        order.status = "confirmed" if p.order_completed or p.order_confirmed else order.status
        order.total = Decimal(str(p.total_price)) if p.total_price else order.total
        if not order.call_id and call_id and call_id != "unknown":
            order.call_id = call_id
        if not order.provider_call_id and p.resolved_provider_call_id():
            order.provider = p.resolved_provider()
            order.provider_call_id = p.resolved_provider_call_id()
        if p.delivery_address and not order.delivery_address:
            order.delivery_address = p.delivery_address
        if p.payment_method and not order.payment_method:
            order.payment_method = p.payment_method
        return order
    order = Order(
        order_number=p.order_number,
        customer_name=customer_name,
        customer_phone=customer_phone,
        email=p.resolved_email(),
        order_type=order_type,
        pickup_time="",
        delivery_address=p.delivery_address or "",
        payment_method=p.payment_method or "",
        status="confirmed" if p.order_completed or p.order_confirmed else "confirmed",
        total=Decimal(str(p.total_price)) if p.total_price else Decimal("0"),
        source="voice_agent",
        call_id=call_id,
        provider=p.resolved_provider(),
        provider_call_id=p.resolved_provider_call_id(),
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


def _payload_text(p: CallEndedWebhook) -> str:
    """Flat transcript text for keyword fallbacks (purpose classification)."""
    parts: list[str] = []
    for t in p.transcript or []:
        if isinstance(t, dict):
            c = t.get("content") or t.get("text") or ""
            if c:
                parts.append(f"{t.get('role', 'unknown')}: {c}")
    return "\n".join(parts)


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
    """Mid-call escalation handoff.

    Dograh calls this when the voice agent transfers to a human; the call
    later ends with a transfer status via /call-ended. Either order works:

    - /transfer first (no Call row yet) → create a lightweight transferred
      placeholder so the Agent View shows the handoff immediately; the later
      /call-ended fills in transcript + summary (duplicate-upgrade path).
    - /call-ended first → just flip that row to transferred here.

    The response's agent_context is what the human agent reads to continue
    the call without making the caller repeat themselves.
    """
    call_id = payload.resolved_call_id()
    if not call_id:
        raise HTTPException(400, "Missing call_id (or workflow_run_id)")
    reason = payload.resolved_reason()
    customer_name = payload.resolved_customer_name()
    customer_phone = payload.resolved_customer_phone()
    call = (
        await db.execute(select(Call).where(Call.call_id == call_id))
    ).scalar_one_or_none()

    if call:
        call.disposition = "transferred"
        if reason:
            call.escalation_reason = reason
        if customer_name and not (call.customer_name or "").strip():
            call.customer_name = customer_name
        if customer_phone and not (call.customer_phone or "").strip():
            call.customer_phone = customer_phone
        if not (call.summary or "").strip():
            # No summary yet (transfer arrived before call end) — write a
            # handoff line now so Agent View has something; call-ended
            # replaces it with the full conversation summary later.
            call.summary = (
                f"Transferred to agent: {reason}. "
                f"Customer {customer_name or customer_phone or 'unknown'} — "
                "full conversation summary arrives when the call ends."
                if reason else
                "Transferred to a human agent. Full conversation summary arrives when the call ends."
            )
    else:
        call = Call(
            call_id=call_id,
            disposition="transferred",
            started_at=datetime.now(timezone.utc),
            ended_at=None,
            duration_seconds=0,
            customer_name=customer_name,
            customer_phone=customer_phone,
            transcript=[],
            tool_calls=[],
            order_numbers=[],
            summary=(
                f"Transferred to agent: {reason}. "
                f"Customer {customer_name or customer_phone or 'unknown'} — "
                "full conversation summary arrives when the call ends."
                if reason else
                "Transferred to a human agent. Full conversation summary arrives when the call ends."
            ),
            escalation_reason=reason,
        )
        db.add(call)
        await db.flush()

    agent_context: dict[str, Any] = {"reason_for_transfer": reason}
    transcript = call.transcript or []
    agent_context["conversation"] = {
        "ai_summary": call.summary or "",
        "transcript_turns": len(transcript),
        # Last turns so the agent can continue mid-conversation.
        "transcript_tail": transcript[-10:] if transcript else [],
        "orders_touched": call.order_numbers or [],
    }
    if customer_phone:
        agent_context["customer"] = {"phone": customer_phone,
                                     "name": customer_name or call.customer_name or ""}
        last_order = (
            await db.execute(
                select(Order)
                .where(Order.customer_phone == customer_phone)
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
    logger.info(f"POST /api/webhooks/transfer | call_id={call_id!r} reason={reason!r}")
    return TransferContextResponse(call_id=call_id, recorded=True,
                                   agent_context=agent_context)
