"""Dashboard: analytics summary (call volume, AI resolution, transfers, revenue)."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Call, Order

router = APIRouter(prefix="/api/analytics", tags=["dashboard"])


@router.get("/summary")
async def summary(db: AsyncSession = Depends(get_db)):
    total_calls = (await db.execute(select(func.count(Call.id)))).scalar() or 0
    ai_resolved = (await db.execute(
        select(func.count(Call.id)).where(Call.disposition == "completed")
    )).scalar() or 0
    transferred = (await db.execute(
        select(func.count(Call.id)).where(Call.disposition == "transferred")
    )).scalar() or 0
    failed = (await db.execute(
        select(func.count(Call.id)).where(Call.disposition.in_(("failed", "abandoned")))
    )).scalar() or 0
    avg_duration = (await db.execute(
        select(func.coalesce(func.avg(Call.duration_seconds), 0))
    )).scalar() or 0
    orders_created = (await db.execute(
        select(func.count(Order.id)).where(Order.status != "incomplete")
    )).scalar() or 0
    revenue = (await db.execute(
        select(func.coalesce(func.sum(Order.total), 0)).where(Order.status != "incomplete")
    )).scalar() or 0

    # last 14 days daily series
    since = datetime.now(timezone.utc) - timedelta(days=13)
    call_rows = (await db.execute(
        select(Call.started_at, Call.disposition).where(Call.started_at >= since)
    )).all()
    order_rows = (await db.execute(
        select(Order.created_at, Order.total).where(Order.created_at >= since)
    )).all()

    daily: dict = {}
    for i in range(14):
        d = (since + timedelta(days=i)).date().isoformat()
        daily[d] = {"date": d, "calls": 0, "transferred": 0, "orders": 0, "revenue": 0.0}
    for started, disp in call_rows:
        key = started.date().isoformat()
        if key in daily:
            daily[key]["calls"] += 1
            if disp == "transferred":
                daily[key]["transferred"] += 1
    for created, total in order_rows:
        key = created.date().isoformat()
        if key in daily:
            daily[key]["orders"] += 1
            daily[key]["revenue"] += float(total or 0)

    return {
        "total_calls": total_calls,
        "ai_resolved": ai_resolved,
        "transferred": transferred,
        "failed": failed,
        "orders_created": orders_created,
        "revenue": float(revenue),
        "avg_duration_seconds": int(avg_duration),
        "daily": list(daily.values()),
    }
