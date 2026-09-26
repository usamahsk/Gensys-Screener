"""Tool endpoints called by the voice agent during live calls."""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.models import Call, Customer, Memory, Order, OrderEvent, OrderItem
from app.schemas import (
    MemoryOut,
    MemoryRecordRequest,
    MemoryResponse,
    OrderCreateRequest,
    OrderCreatedResponse,
    OrderItemOut,
    OrderStatusResponse,
    clean_str,
)
from app.services.embeddings import embed_one

router = APIRouter(prefix="/api/tools", tags=["agent-tools"])


# Order numbers: PB- + 3 phone-safe chars (no I/L/O/0/1 — unambiguous over voice).
_ORDER_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _gen_order_number() -> str:
    return f"PB-{''.join(secrets.choice(_ORDER_CODE_ALPHABET) for _ in range(3))}"


async def _gen_unique_order_number(db: AsyncSession) -> str:
    """Generate a PB- code not already in use (the 3-char space is small)."""
    for _ in range(10):
        num = _gen_order_number()
        exists = (
            await db.execute(select(Order.id).where(Order.order_number == num))
        ).scalar_one_or_none()
        if exists is None:
            return num
    # Practically unreachable — fall back to a longer code rather than failing.
    return f"PB-{''.join(secrets.choice(_ORDER_CODE_ALPHABET) for _ in range(6))}"


async def _log_tool_call(
    db: AsyncSession,
    *,
    call_id: str,
    call_type: str,
    customer_name: str = "",
    customer_phone: str = "",
    first_name: str = "",
    rsvp: str = "",
    duration: int = 0,
    recording_url: str = "",
    user_recording_url: str = "",
    bot_recording_url: str = "",
    transcript_url: str = "",
    order_number: str = "",
    tool_name: str = "",
    tool_summary: str = "",
    provider: str = "",
    provider_call_id: str = "",
) -> Call | None:
    """Create or update the Call Logs row for this tool call.

    Every create_order / order_status call with a call_id lands here so the
    dashboard's Call Logs section shows one row per call with its type.
    """
    call_id = (call_id or "").strip()
    if not call_id:
        return None
    try:
        duration = int(duration or 0)
    except (ValueError, TypeError):
        duration = 0
    call = (
        await db.execute(select(Call).where(Call.call_id == call_id))
    ).scalar_one_or_none()
    if call is None:
        call = Call(
            call_id=call_id,
            call_type=call_type,
            disposition="completed",
            started_at=datetime.now(timezone.utc) - timedelta(seconds=duration),
            ended_at=datetime.now(timezone.utc),
            duration_seconds=duration,
            customer_name=customer_name or "",
            customer_phone=customer_phone or "",
            first_name=first_name or "",
            rsvp=rsvp or "",
            recording_url=recording_url or "",
            user_recording_url=user_recording_url or "",
            bot_recording_url=bot_recording_url or "",
            transcript_url=transcript_url or "",
            order_numbers=[order_number] if order_number else [],
            tool_calls=[{"name": tool_name, "args_summary": tool_summary}] if tool_name else [],
            summary=tool_summary,
            provider=(provider or "dograh")[:32],
            provider_call_id=(provider_call_id or "")[:128],
        )
        db.add(call)
        await db.flush()
        return call
    # update existing row with anything new the tool call carried
    if call_type and not call.call_type:
        call.call_type = call_type
    if customer_name and not call.customer_name:
        call.customer_name = customer_name
    if customer_phone and not call.customer_phone:
        call.customer_phone = customer_phone
    if first_name and not call.first_name:
        call.first_name = first_name
    if rsvp and not call.rsvp:
        call.rsvp = rsvp
    if duration and not call.duration_seconds:
        call.duration_seconds = duration
    if recording_url and not call.recording_url:
        call.recording_url = recording_url
    if user_recording_url and not call.user_recording_url:
        call.user_recording_url = user_recording_url
    if bot_recording_url and not call.bot_recording_url:
        call.bot_recording_url = bot_recording_url
    if transcript_url and not call.transcript_url:
        call.transcript_url = transcript_url
    if provider_call_id and not call.provider_call_id:
        call.provider_call_id = provider_call_id[:128]
    if provider and not (call.provider and call.provider != "dograh"):
        call.provider = provider[:32]
    if order_number and order_number not in (call.order_numbers or []):
        call.order_numbers = [*(call.order_numbers or []), order_number]
    if tool_name:
        call.tool_calls = [*(call.tool_calls or []), {"name": tool_name, "args_summary": tool_summary}]
    await db.flush()
    return call


# --------------------------------------------------------------- order create

@router.post("/orders", response_model=OrderCreatedResponse, status_code=201)
async def create_order(
    req: OrderCreateRequest, request: Request, db: AsyncSession = Depends(get_db)
):
    """Record an order exactly as the voice agent sends it.

    Items are NOT validated against any menu/catalog — whatever the tool call
    carries (name, quantity, unit price) is stored verbatim. Totals come from
    the per-item prices, or from the explicit `total` field as fallback.

    No order row is created until there is real data: a call with no
    phone/items only logs the call and returns a pending response — the
    post-call transcript extraction creates the order. The UI never shows
    empty shells.
    """
    from decimal import Decimal as D

    logger.info(
        f"POST /api/tools/orders from {request.client.host if request.client else '?'} "
        f"| phone={req.customer_phone!r} name={req.customer_name!r} "
        f"type={req.order_type!r} items={len(req.items)} total={req.total!r} "
        f"call_id={req.resolved_call_id()!r}"
    )
    if not req.customer_phone.strip() or not req.items:
        await _log_tool_call(
            db,
            call_id=req.resolved_call_id(),
            call_type="create_order",
            customer_name=req.customer_name.strip(),
            customer_phone=req.customer_phone.strip(),
            first_name=req.resolved_first_name(),
            rsvp=clean_str(req.rsvp),
            duration=req.resolved_duration(),
            recording_url=clean_str(req.recording_url),
            user_recording_url=clean_str(req.user_recording_url),
            bot_recording_url=clean_str(req.bot_recording_url),
            transcript_url=clean_str(req.transcript_url),
            order_number="",
            tool_name="create_order",
            tool_summary="noted (pending transcript extraction)",
            provider=req.resolved_provider(),
            provider_call_id=req.resolved_provider_call_id(),
        )
        await db.commit()
        return OrderCreatedResponse(
            order_number="", total=D("0"), status="pending", items=[],
        )

    total = 0.0
    priced = False
    for it in req.items:
        if (it.unit_price or 0) > 0:
            priced = True
            total += float(it.unit_price) * it.quantity
    if not priced and req.total:
        total = float(req.total)

    # $2.50 delivery fee on delivery orders below $25 (same rule as extraction).
    from app.services.order_recovery import DELIVERY_FEE, FREE_DELIVERY_ABOVE
    fee_applied = bool(clean_str(req.delivery_address)) and total < FREE_DELIVERY_ABOVE
    if fee_applied:
        total = round(total + DELIVERY_FEE, 2)

    order_number = await _gen_unique_order_number(db)
    order = Order(
        order_number=order_number,
        customer_name=req.customer_name.strip(),
        customer_phone=req.customer_phone.strip(),
        email=clean_str(req.email),
        order_type=req.order_type,
        table_number=clean_str(req.table_number),
        pickup_time=clean_str(req.pickup_time),
        delivery_address=clean_str(req.delivery_address),
        payment_method=clean_str(req.payment_method),
        status="confirmed",
        total=D(str(round(total, 2))),
        call_id=req.resolved_call_id(),
        provider=req.resolved_provider(),
        provider_call_id=req.resolved_provider_call_id(),
    )
    db.add(order)
    await db.flush()

    for it in req.items:
        db.add(
            OrderItem(
                order_id=order.id,
                menu_item_id=None,
                sku=(it.sku or "").strip(),
                name=(it.name or it.sku or "Item").strip(),
                quantity=it.quantity,
                unit_price=D(str(it.unit_price or 0)),
                size=it.size or "",
                notes=it.notes or "",
            )
        )
    if fee_applied:
        db.add(OrderItem(order_id=order.id, menu_item_id=None, sku="",
                         name="Delivery fee", quantity=1,
                         unit_price=D(str(DELIVERY_FEE)), size="", notes=""))
    db.add(OrderEvent(order_id=order.id, status="confirmed", note="Order placed via voice agent"))
    await db.flush()

    # customer + memory for continuation on future calls
    cust = (await db.execute(
        select(Customer).where(Customer.phone == order.customer_phone)
    )).scalar_one_or_none()
    if not cust:
        cust = Customer(phone=order.customer_phone)
        db.add(cust)
        await db.flush()
    cust.name = cust.name or order.customer_name
    cust.email = cust.email or order.email
    cust.last_order_number = order.order_number
    cust.last_intent = "order_placement"
    item_desc = ", ".join(
        f"{it.quantity}x {(it.name or it.sku or 'Item').strip()}" for it in req.items
    )
    await _add_memory(
        db, cust, "order",
        f"Placed order {order.order_number}: {item_desc}. Total ${order.total}. "
        f"Type: {order.order_type}"
        + (f", table {order.table_number}" if order.table_number else "")
        + (f", pickup {order.pickup_time}" if order.pickup_time else "") + ".",
    )

    # call log — every order creation lands in Call Logs with type create_order
    await _log_tool_call(
        db,
        call_id=req.resolved_call_id(),
        call_type="create_order",
        customer_name=order.customer_name,
        customer_phone=order.customer_phone,
        first_name=req.resolved_first_name(),
        rsvp=clean_str(req.rsvp),
        duration=req.resolved_duration(),
        recording_url=clean_str(req.recording_url),
        user_recording_url=clean_str(req.user_recording_url),
        bot_recording_url=clean_str(req.bot_recording_url),
        transcript_url=clean_str(req.transcript_url),
        order_number=order.order_number,
        tool_name="create_order",
            tool_summary=f"{order.order_number}: {item_desc} (${order.total})",
        provider=req.resolved_provider(),
        provider_call_id=req.resolved_provider_call_id(),
    )

    await db.commit()
    await db.refresh(order)
    return OrderCreatedResponse(
        order_number=order.order_number,
        total=order.total,
        status=order.status,
        items=[
            OrderItemOut(
                sku=(it.sku or "").strip(),
                name=(it.name or it.sku or "Item").strip(),
                quantity=it.quantity,
                unit_price=it.unit_price or 0,
                size=it.size or "",
                notes=it.notes or "",
            )
            for it in req.items
        ],
    )


# --------------------------------------------------------------- order status

@router.get("/orders/{order_number}", response_model=OrderStatusResponse)
async def order_status(
    order_number: str,
    db: AsyncSession = Depends(get_db),
    call_id: Optional[str] = Query(default="", description="Call id, e.g. {{workflow_run_id}}"),
    first_name: Optional[str] = Query(default=""),
    rsvp: Optional[str] = Query(default=""),
    duration: Optional[str] = Query(default=""),
    recording_url: Optional[str] = Query(default=""),
    user_recording_url: Optional[str] = Query(default=""),
    bot_recording_url: Optional[str] = Query(default=""),
    transcript_url: Optional[str] = Query(default=""),
):
    result = await _lookup_order(order_number, db)
    await _log_tool_call(
        db,
        call_id=call_id or "",
        call_type="order_status",
        first_name=clean_str(first_name),
        rsvp=clean_str(rsvp),
        duration=_coerce_int(duration),
        recording_url=clean_str(recording_url),
        user_recording_url=clean_str(user_recording_url),
        bot_recording_url=clean_str(bot_recording_url),
        transcript_url=clean_str(transcript_url),
        order_number=order_number,
        tool_name="order_status",
        tool_summary=order_number,
        customer_name=result.order.customer_name if result.found and result.order else "",
        customer_phone=result.order.customer_phone if result.found and result.order else "",
    )
    await db.commit()
    return result


@router.get("/order-status", response_model=OrderStatusResponse)
async def order_status_query(
    order_number: str = Query(..., description="Order number, e.g. PB-A1B"),
    db: AsyncSession = Depends(get_db),
    call_id: Optional[str] = Query(default="", description="Call id, e.g. {{workflow_run_id}}"),
    first_name: Optional[str] = Query(default=""),
    rsvp: Optional[str] = Query(default=""),
    duration: Optional[str] = Query(default=""),
    recording_url: Optional[str] = Query(default=""),
    user_recording_url: Optional[str] = Query(default=""),
    bot_recording_url: Optional[str] = Query(default=""),
    transcript_url: Optional[str] = Query(default=""),
):
    """Query-param variant — friendlier for tool platforms without path params."""
    result = await _lookup_order(order_number, db)
    await _log_tool_call(
        db,
        call_id=call_id or "",
        call_type="order_status",
        first_name=clean_str(first_name),
        rsvp=clean_str(rsvp),
        duration=_coerce_int(duration),
        recording_url=clean_str(recording_url),
        user_recording_url=clean_str(user_recording_url),
        bot_recording_url=clean_str(bot_recording_url),
        transcript_url=clean_str(transcript_url),
        order_number=order_number,
        tool_name="order_status",
        tool_summary=order_number,
        customer_name=result.order.customer_name if result.found and result.order else "",
        customer_phone=result.order.customer_phone if result.found and result.order else "",
    )
    await db.commit()
    return result


def _coerce_int(v: object) -> int:
    if v in (None, ""):
        return 0
    try:
        return int(float(str(v)))
    except (ValueError, TypeError):
        return 0


async def _lookup_order(order_number: str, db: AsyncSession) -> OrderStatusResponse:
    order = (
        await db.execute(
            select(Order)
            .options(selectinload(Order.items), selectinload(Order.events))
            .where(func.upper(Order.order_number) == order_number.upper().strip())
        )
    ).scalar_one_or_none()
    if not order:
        return OrderStatusResponse(found=False, message=f"No order found for {order_number}")
    return OrderStatusResponse(found=True, order=order)  # type: ignore[arg-type]


# ----------------------------------------------------------------- memory

async def _add_memory(db: AsyncSession, customer: Customer, kind: str, text: str) -> None:
    vec = await embed_one(text)
    db.add(Memory(customer_id=customer.id, kind=kind, text=text, embedding=vec))


@router.get("/memory/{phone}", response_model=MemoryResponse)
async def get_memory(phone: str, db: AsyncSession = Depends(get_db)):
    return await _get_memory_impl(phone, db)


@router.get("/memory", response_model=MemoryResponse)
async def get_memory_query(
    phone: str = Query(..., description="Customer phone number"),
    db: AsyncSession = Depends(get_db),
):
    """Query-param variant — friendlier for tool platforms without path params."""
    return await _get_memory_impl(phone, db)


async def _get_memory_impl(phone: str, db: AsyncSession) -> MemoryResponse:
    phone = phone.strip()
    cust = (await db.execute(select(Customer).where(Customer.phone == phone))).scalar_one_or_none()
    if not cust:
        return MemoryResponse(phone=phone, name="", last_intent="", last_order_number="",
                              context={}, recent_memories=[])
    recent = (await db.execute(
        select(Memory).where(Memory.customer_id == cust.id)
        .order_by(Memory.created_at.desc()).limit(10)
    )).scalars().all()
    return MemoryResponse(
        phone=phone, name=cust.name or "", last_intent=cust.last_intent or "",
        last_order_number=cust.last_order_number or "", context=cust.context or {},
        recent_memories=recent,  # type: ignore[arg-type]
    )


@router.post("/memory/{phone}", response_model=MemoryOut, status_code=201)
async def record_memory(phone: str, req: MemoryRecordRequest, db: AsyncSession = Depends(get_db)):
    cust = (await db.execute(select(Customer).where(Customer.phone == phone.strip()))).scalar_one_or_none()
    if not cust:
        cust = Customer(phone=phone.strip())
        db.add(cust)
        await db.flush()
    vec = await embed_one(req.text)
    mem = Memory(customer_id=cust.id, kind=req.kind, text=req.text, embedding=vec)
    db.add(mem)
    await db.commit()
    await db.refresh(mem)
    return mem  # type: ignore[return-value]
