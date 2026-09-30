"""Dashboard: orders list + detail with tracking timeline."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.models import Order, OrderEvent

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
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List orders. `status=incomplete` shows only follow-up orders;
    `exclude_incomplete=true` hides them from the fulfilment queue."""
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
    return [
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
            "provider": o.provider or "dograh",
            "provider_call_id": o.provider_call_id or "",
            "call_id": o.call_id or "",
        }
        for o in rows
    ]


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
        "provider": order.provider or "dograh",
        "provider_call_id": order.provider_call_id or "",
        "created_at": order.created_at.isoformat(),
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
