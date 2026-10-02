"""Dashboard: orders list + detail with tracking timeline."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.models import Call, Order, OrderEvent

router = APIRouter(prefix="/api/orders", tags=["dashboard"])

VALID_STATUSES = {"confirmed", "preparing", "ready", "in_transit", "completed", "cancelled", "incomplete"}


@router.get("/next-number")
async def next_order_number(db: AsyncSession = Depends(get_db)):
    """Total order count + the next sequential number (total + 1).

    The voice workflow calls this before creating an order so order numbers
    stay sequential: next_order_number = total_orders + 1. (Must be declared
    before /{order_number} or FastAPI would treat "next-number" as an id.)
    """
    total = (await db.execute(select(func.count()).select_from(Order))).scalar_one()
    return {"total_orders": total, "next_order_number": total + 1}


class StatusUpdateRequest(BaseModel):
    status: str = Field(pattern="^(confirmed|preparing|ready|in_transit|completed|cancelled|incomplete)$")
    note: str = ""


@router.patch("/{order_number}/status")
async def update_status(order_number: str, req: StatusUpdateRequest, db: AsyncSession = Depends(get_db)):
    order = (
        await db.execute(select(Order).where(Order.order_number == order_number))
    ).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    order.status = req.status
    db.add(OrderEvent(order_id=order.id, status=req.status, note=req.note or "Updated by staff"))
    await db.commit()
    return {"order_number": order.order_number, "status": order.status}


@router.get("")
async def list_orders(
    status: Optional[str] = None,
    order_type: Optional[str] = None,
    search: Optional[str] = None,
    source: Optional[str] = None,
    exclude_incomplete: bool = False,
    has_provider_id: bool = False,
    has_run: bool = False,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List orders. `status=incomplete` shows only follow-up orders;
    `exclude_incomplete=true` hides them from the fulfilment queue.
    `has_provider_id` keeps only orders with a provider/telephone id;
    `has_run` keeps only orders whose linked call has a Dograh run id."""
    q = select(Order).options(selectinload(Order.items)).order_by(Order.created_at.desc()).limit(limit)
    if status:
        q = q.where(Order.status == status)
    elif exclude_incomplete:
        q = q.where(Order.status != "incomplete")
    if order_type:
        q = q.where(Order.order_type == order_type)
    if source:
        q = q.where(Order.source == source.strip())
    if search:
        like = f"%{search.strip()}%"
        q = q.where(
            Order.order_number.ilike(like)
            | Order.customer_name.ilike(like)
            | Order.customer_phone.ilike(like)
            | Order.provider_call_id.ilike(like)
            | Order.call_id.ilike(like)
        )
    rows = (await db.execute(q)).scalars().all()
    # Attach the linked call's run/provider chain so the Transactions
    # (orders-only) tab can show + jump to the linked Call Log.
    calls_by_id: dict[str, Call] = {}
    want_ids = {(o.call_id or "").strip() for o in rows if (o.call_id or "").strip()}
    if want_ids:
        for c in (await db.execute(
                select(Call).where(Call.call_id.in_(want_ids)))).scalars().all():
            calls_by_id[c.call_id] = c
    # Orders linked via call.order_numbers (order created before webhook
    # backfill stamped call_id) — resolve run chain through those too.
    numbers = {o.order_number for o in rows}
    if numbers:
        for c in (await db.execute(select(Call))).scalars().all():
            for n in (c.order_numbers or []):
                if n in numbers and c.call_id not in calls_by_id:
                    # Only use as fallback when the order has no direct call_id.
                    calls_by_id.setdefault(f"order:{n}", c)
    out = []
    for o in rows:
        linked = calls_by_id.get((o.call_id or "").strip() or f"order:{o.order_number}")
        eff_provider_id = (o.provider_call_id or "").strip() or (
            (linked.provider_call_id or "").strip() if linked else "")
        eff_genesys = (linked.genesys_conversation_id or "").strip() if linked else ""
        if has_provider_id and not (eff_provider_id or eff_genesys):
            continue
        if has_run and not (linked.run_id if linked else None):
            continue
        out.append(
            {
                "order_number": o.order_number,
                "customer_name": o.customer_name,
                "customer_phone": o.customer_phone,
                "order_type": o.order_type,
                "table_number": o.table_number,
                "pickup_time": o.pickup_time,
                "delivery_address": o.delivery_address,
                "payment_method": o.payment_method,
                "status": o.status,
                "total": float(o.total),
                "source": o.source,
                "item_count": len(o.items),
                "created_at": o.created_at.isoformat(),
                "provider": o.provider or (linked.provider if linked else None) or "dograh",
                "provider_call_id": o.provider_call_id or (linked.provider_call_id if linked else None) or "",
                "call_id": o.call_id or "",
                "run_id": linked.run_id if linked else None,
                "workflow_id": linked.workflow_id if linked else None,
                "workflow_name": (linked.workflow_name if linked else None) or "",
                "genesys_conversation_id": (linked.genesys_conversation_id if linked else None) or "",
                "customer_intent": (linked.customer_intent if linked else None) or (linked.call_purpose if linked else None) or "",
                "call_purpose": (linked.call_purpose if linked else None) or "",
            }
        )
    return out


@router.get("/{order_number}")
async def order_detail(order_number: str, db: AsyncSession = Depends(get_db)):
    order = (
        await db.execute(
            select(Order)
            .options(selectinload(Order.items), selectinload(Order.events))
            .where(Order.order_number == order_number)
        )
    ).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    linked: Call | None = None
    if (order.call_id or "").strip():
        linked = (await db.execute(
            select(Call).where(Call.call_id == order.call_id.strip())
        )).scalar_one_or_none()
    if linked is None:
        for c in (await db.execute(select(Call))).scalars().all():
            if order.order_number in (c.order_numbers or []):
                linked = c
                break
    return {
        "order_number": order.order_number,
        "customer_name": order.customer_name,
        "customer_phone": order.customer_phone,
        "email": order.email,
        "order_type": order.order_type,
        "table_number": order.table_number,
        "pickup_time": order.pickup_time,
        "delivery_address": order.delivery_address,
        "payment_method": order.payment_method,
        "status": order.status,
        "total": float(order.total),
        "source": order.source,
        "call_id": order.call_id,
        "provider": order.provider or (linked.provider if linked else None) or "dograh",
        "provider_call_id": order.provider_call_id or (linked.provider_call_id if linked else None) or "",
        "run_id": linked.run_id if linked else None,
        "workflow_id": linked.workflow_id if linked else None,
        "workflow_name": (linked.workflow_name if linked else None) or "",
        "genesys_conversation_id": (linked.genesys_conversation_id if linked else None) or "",
        "customer_intent": (linked.customer_intent if linked else None) or (linked.call_purpose if linked else None) or "",
        "call_purpose": (linked.call_purpose if linked else None) or "",
        "item_count": len(order.items),
        "created_at": order.created_at.isoformat(),
        "linked_call": (
            {
                "call_id": linked.call_id,
                "provider": linked.provider or "dograh",
                "provider_call_id": linked.provider_call_id or "",
                "genesys_conversation_id": linked.genesys_conversation_id or "",
                "run_id": linked.run_id,
                "workflow_id": linked.workflow_id,
                "call_purpose": linked.call_purpose or "",
                "customer_intent": (linked.customer_intent or linked.call_purpose or "") or "",
            }
            if linked else None
        ),
        "items": [
            {
                "sku": i.sku, "name": i.name, "quantity": i.quantity,
                "unit_price": float(i.unit_price), "size": i.size, "notes": i.notes,
            }
            for i in order.items
        ],
        "events": [
            {"status": e.status, "note": e.note, "created_at": e.created_at.isoformat()}
            for e in order.events
        ],
    }
