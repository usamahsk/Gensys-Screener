"""Dashboard: unified Transactions — one row per call with its linked orders.

Merges what used to be three separate views (Call Logs, Orders, Runs) so
there is a single source of truth: each transaction carries its call record,
its linked orders (items + timeline), and the provider chain
(provider / provider id / genesys id / run id).
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.models import Call, Order
from app.services import dograh_client

router = APIRouter(prefix="/api/transactions", tags=["dashboard"])


def _looks_like_sid(v: str) -> bool:
    import re

    s = (v or "").strip()
    # Provider session ids (UUIDs, hex SIDs). Purely numeric Dograh run ids
    # are deliberately NOT accepted — those resolve via run lookup below so
    # the stored provider id is always the real context id, never the run id.
    return bool(
        re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", s, re.I)
        or re.match(r"^[A-Za-z]{0,4}[0-9a-f-]{16,}$", s, re.I)
    )


def _call_dict(c: Call) -> dict:
    return {
        "call_id": c.call_id,
        "call_type": c.call_type or "",
        "call_purpose": c.call_purpose or "",
        "disposition": c.disposition,
        "started_at": c.started_at.isoformat() if c.started_at else None,
        "duration_seconds": c.duration_seconds,
        "customer_name": c.customer_name,
        "customer_phone": c.customer_phone,
        "first_name": c.first_name or "",
        "rsvp": c.rsvp or "",
        "summary": c.summary,
        "escalation_reason": c.escalation_reason,
        "order_numbers": c.order_numbers or [],
        "sentiment": c.sentiment or "",
        "recording_url": c.recording_url or "",
        "transcript_url": c.transcript_url or "",
        "provider": c.provider or "dograh",
        "provider_call_id": c.provider_call_id or "",
        "genesys_conversation_id": c.genesys_conversation_id or "",
        "run_id": c.run_id,
        "workflow_id": c.workflow_id,
        "workflow_name": c.workflow_name or "",
        "customer_intent": (c.customer_intent or c.call_purpose or "") or "",
        "initial_context": c.initial_context or {},
        "gathered_context": c.gathered_context or {},
    }


def _order_summary(o: Order) -> dict:
    return {
        "order_number": o.order_number,
        "customer_name": o.customer_name,
        "customer_phone": o.customer_phone,
        "order_type": o.order_type,
        "table_number": o.table_number,
        "pickup_time": o.pickup_time,
        "delivery_address": o.delivery_address,
        "payment_method": o.payment_method,
        "status": o.status,
        "total": float(o.total or 0),
        "source": o.source,
        "item_count": len(o.items),
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "provider": o.provider or "dograh",
        "provider_call_id": o.provider_call_id or "",
        "call_id": o.call_id or "",
    }


@router.get("")
async def list_sessions(
    disposition: Optional[str] = None,
    purpose: Optional[str] = None,
    order_status: Optional[str] = None,
    search: Optional[str] = None,
    linked_only: bool = False,
    has_provider_id: bool = False,
    has_run: bool = False,
    limit: int = Query(default=100, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List transactions. Filters: `purpose` = call intent (order_creation |
    order_status | reservation | general); `order_status` = linked-order
    status incl. `incomplete` (sessions whose linked order has that status).
    `linked_only` = only rows with ≥1 linked order (the Orders view);
    `has_provider_id` = only rows with a provider/telephone id or Genesys
    conversation id; `has_run` = only rows linked to a Dograh run id."""
    cq = select(Call).order_by(Call.started_at.desc()).limit(limit)
    if disposition:
        cq = cq.where(Call.disposition == disposition)
    if purpose:
        cq = cq.where(Call.call_purpose == purpose.strip())
    if search:
        like = f"%{search.strip()}%"
        cq = cq.where(
            Call.summary.ilike(like)
            | Call.customer_name.ilike(like)
            | Call.customer_phone.ilike(like)
            | Call.call_id.ilike(like)
            | Call.first_name.ilike(like)
            | Call.provider_call_id.ilike(like)
            | Call.provider.ilike(like)
            | Call.genesys_conversation_id.ilike(like)
        )
    calls = (await db.execute(cq)).scalars().all()

    order_numbers: set[str] = set()
    for c in calls:
        order_numbers.update(c.order_numbers or [])
    orders_by_number: dict[str, Order] = {}
    if order_numbers:
        orows = (
            await db.execute(
                select(Order)
                .options(selectinload(Order.items))
                .where(Order.order_number.in_(order_numbers))
            )
        ).scalars().all()
        orders_by_number = {o.order_number: o for o in orows}

    # Transactions that exist as orders but have no call row (e.g. webhook-only).
    call_ids = {c.call_id for c in calls}
    oq = select(Order).options(selectinload(Order.items)).order_by(Order.created_at.desc()).limit(limit)
    if search:
        like = f"%{search.strip()}%"
        oq = oq.where(
            Order.order_number.ilike(like)
            | Order.customer_name.ilike(like)
            | Order.customer_phone.ilike(like)
            | Order.provider_call_id.ilike(like)
            | Order.call_id.ilike(like)
        )
    orphans = [
        o for o in (await db.execute(oq)).scalars().all()
        if o.order_number not in order_numbers
        and (not o.call_id or o.call_id not in call_ids)
    ]

    transactions = []
    for c in calls:
        linked = [orders_by_number[n] for n in (c.order_numbers or []) if n in orders_by_number]
        if order_status:
            want = order_status.strip()
            if not any(o.status == want for o in linked):
                continue
        if linked_only and not linked:
            continue
        if has_run and not c.run_id:
            continue
        if has_provider_id and not ((c.provider_call_id or "").strip()
                                     or (c.genesys_conversation_id or "").strip()):
            continue
        transactions.append({
            "key": f"call:{c.call_id}",
            "started_at": c.started_at.isoformat() if c.started_at else "",
            "call": _call_dict(c),
            "orders": [_order_summary(o) for o in linked],
        })
    for o in orphans:
        if order_status and o.status != order_status.strip():
            continue
        if has_run:
            continue  # orphan orders have no run link
        if has_provider_id and not (o.provider_call_id or "").strip():
            continue
        transactions.append({
            "key": f"order:{o.order_number}",
            "started_at": o.created_at.isoformat() if o.created_at else "",
            "call": None,
            "orders": [_order_summary(o)],
        })
    transactions.sort(key=lambda s: s["started_at"] or "", reverse=True)
    return {"transactions": transactions, "total": len(transactions)}


# Customer keys inside a Dograh run's gathered/initial context (mirrors the
# nested fallback in CallEndedWebhook). The ANI phone arrives as
# caller_number / from_number in initial_context.
_CTX_PHONE_KEYS = (
    "customer_phone", "phone", "phone_number", "phoneNumber",
    "caller_number", "callerNumber", "from_number", "from",
    "msisdn", "ani", "caller_id", "callerId",
)
_CTX_NAME_KEYS = (
    "customer_name", "name", "full_name", "fullname",
    "first_name", "firstname", "caller_name", "callerName",
)


def _first_ctx_value(scopes: list[dict], keys: tuple[str, ...]) -> str:
    for scope in scopes:
        if not isinstance(scope, dict):
            continue
        lowered = {str(k).strip().lower(): v for k, v in scope.items()}
        for key in keys:
            v = scope.get(key, lowered.get(key.lower()))
            if isinstance(v, str) and v.strip() and "{{" not in v:
                return v.strip()
    return ""


@router.post("/backfill-provider-ids", response_model=dict)
async def backfill_provider_ids(db: AsyncSession = Depends(get_db)):
    """Permanently fill empty provider IDs on existing rows.

    The provider id always comes from the run's context (gathered/initial
    context ids, Genesys AudioHook session/conversation) — never the numeric
    Dograh run id. Resolution per call:
    - genesys conversation id already stored → use it
    - run_id/workflow_id known → Dograh run detail → context ids
    - numeric call_id (webhook {{workflow_run_id}}) → find the run on Dograh,
      link run_id/workflow_id, then run detail → context ids
    - call_id that already looks like a session UUID → store it
    Linked orders inherit the id from their call.
    Also fills customer name/phone that the post-call webhook never carried
    (Dograh keeps the ANI phone in the run's initial/gathered context) —
    this repairs old transactions that show an empty customer.
    """
    import json as _json

    from app.routers.integration import _extract_telephony, _get_setting

    try:
        setting = await _get_setting(db)
        api_key = setting.api_key or ""
        base_url = setting.base_url or ""
    except Exception:
        api_key, base_url = "", ""

    all_calls = (await db.execute(select(Call))).scalars().all()
    calls_by_id = {c.call_id: c for c in all_calls}
    orders_by_number: dict[str, Order] = {}
    if all_calls:
        nums: set[str] = set()
        for c in all_calls:
            nums.update(c.order_numbers or [])
        if nums:
            for o in (await db.execute(
                    select(Order).options(selectinload(Order.items))
                    .where(Order.order_number.in_(nums)))).scalars().all():
                orders_by_number[o.order_number] = o

    updated_calls = 0
    updated_customers = 0
    runs_scanned = 0
    for c in all_calls:
        need_customer = not (c.customer_phone or "").strip() or not (c.customer_name or "").strip()
        # A Genesys row whose Provider ID is the session id (not the
        # conversation id) still needs one run-detail pass to realign it.
        need_realign = (c.provider or "") == "genesys" and not (c.genesys_conversation_id or "").strip()
        if c.provider_call_id and not need_customer and not need_realign:
            continue
        pid, prov = "", ""
        ctx_scopes: list[dict] = []
        if c.genesys_conversation_id:
            pid, prov = c.genesys_conversation_id, "genesys"
        elif c.run_id and c.workflow_id and api_key:
            try:
                detail = await dograh_client.get_run_detail(
                    api_key, base_url, c.workflow_id, c.run_id)
                tele = _extract_telephony(detail)
                pid, prov = tele["provider_call_id"], tele["provider"]
                ctx_scopes = [detail.get("gathered_context") or {},
                              detail.get("initial_context") or {}]
            except Exception:
                pid, prov = "", ""
            runs_scanned += 1
        elif c.call_id and c.call_id.strip().isdigit() and api_key:
            # Webhook row whose call_id is the numeric Dograh run id:
            # locate the run to learn its workflow, link it, then read
            # the provider id out of the run's context.
            try:
                run_id_int = int(c.call_id.strip())
                filt = _json.dumps([{"attribute": "runId", "type": "number",
                                     "value": {"value": run_id_int}}])
                data = await dograh_client.list_runs(
                    api_key, base_url, page=1, limit=10, filters=filt)
                runs = data.get("runs", []) or []
                match = next((r for r in runs if str(r.get("id")) == c.call_id.strip()),
                             runs[0] if runs else None)
                if match and match.get("workflow_id"):
                    wf = match["workflow_id"]
                    tele = _extract_telephony(match)
                    ctx_scopes = [match.get("gathered_context") or {},
                                  match.get("initial_context") or {}]
                    if not tele["provider_call_id"]:
                        detail = await dograh_client.get_run_detail(
                            api_key, base_url, wf, run_id_int)
                        tele = _extract_telephony(detail)
                        ctx_scopes = [detail.get("gathered_context") or {},
                                      detail.get("initial_context") or {}]
                    pid, prov = tele["provider_call_id"], tele["provider"]
                    if pid:
                        c.run_id, c.workflow_id = run_id_int, wf
                        if not c.workflow_name and match.get("workflow_name"):
                            c.workflow_name = match["workflow_name"]
            except Exception:
                pid, prov = "", ""
            runs_scanned += 1
        elif c.call_id and not c.call_id.startswith("voice_") and _looks_like_sid(c.call_id):
            pid, prov = c.call_id.strip()[:128], c.provider or "dograh"
        # Prefer the true conversation id over a stored session id.
        conv = ""
        for _scope in ctx_scopes:
            if isinstance(_scope, dict):
                _nested: list[dict] = [_scope] + [
                    v for v in _scope.values() if isinstance(v, dict)]
                for _s in _nested:
                    for _k in ("conversation_id", "conversationId",
                               "genesys_conversation_id", "genesysConversationId"):
                        _v = _s.get(_k)
                        if isinstance(_v, str) and _v.strip() and "{{" not in _v:
                            conv = _v.strip()[:64]
                            break
                    if conv:
                        break
            if conv:
                break
        if conv:
            if (prov or "") == "genesys" or (c.provider or "") == "genesys":
                pid, prov = conv, "genesys"
            c.genesys_conversation_id = conv
        if pid:
            if not (c.provider_call_id or "").strip() or (
                    conv and (c.provider_call_id or "").strip() != conv
                    and ((c.provider or "") == "genesys" or (prov or "") == "genesys")):
                c.provider_call_id = pid[:128]
            if prov and (not c.provider or c.provider == "dograh"):
                c.provider = prov[:32]
            updated_calls += 1
        elif conv and not (c.genesys_conversation_id or "").strip():
            c.genesys_conversation_id = conv
            updated_calls += 1
        if need_customer and ctx_scopes:
            ph = _first_ctx_value(ctx_scopes, _CTX_PHONE_KEYS)
            nm = _first_ctx_value(ctx_scopes, _CTX_NAME_KEYS)
            if ph and not (c.customer_phone or "").strip():
                c.customer_phone = ph[:32]
                updated_customers += 1
            if nm and not (c.customer_name or "").strip():
                c.customer_name = nm[:128]
                updated_customers += 1

    updated_orders = 0
    all_orders = (await db.execute(
        select(Order).options(selectinload(Order.items)))).scalars().all()
    for o in all_orders:
        linked = calls_by_id.get(o.call_id or "")
        if linked is None:
            for c in all_calls:
                if o.order_number in (c.order_numbers or []):
                    linked = c
                    break
        if linked is not None and linked.provider_call_id and not o.provider_call_id:
            o.provider_call_id = linked.provider_call_id
            if not o.provider or o.provider == "dograh":
                o.provider = linked.provider or "dograh"
            updated_orders += 1
        elif not o.provider_call_id and o.call_id and not o.call_id.startswith("voice_") and _looks_like_sid(o.call_id):
            o.provider_call_id = o.call_id.strip()[:128]
            updated_orders += 1
        # Orders saved before the webhook carried customer details inherit
        # them from their (possibly backfilled) call.
        if linked is not None:
            if not (o.customer_phone or "").strip() and (linked.customer_phone or "").strip():
                o.customer_phone = linked.customer_phone
                updated_customers += 1
            if not (o.customer_name or "").strip() and (linked.customer_name or "").strip():
                o.customer_name = linked.customer_name
                updated_customers += 1

    await db.commit()
    return {"updated_calls": updated_calls, "updated_orders": updated_orders,
            "updated_customers": updated_customers, "runs_scanned": runs_scanned}


_TYPE_TO_PURPOSE = {"create_order": "order_creation",
                    "order_status": "order_status",
                    "reservation": "reservation"}

_RESERVATION_KW = ("book a table", "reserve a table", "reservation",
                   "table for ", "party of ", "party size")
_STATUS_KW = ("order status", "status of my order", "status check",
              "where is my order", "is my order", "track my order",
              "cancel my order", "how long for my order")


def _infer_call_purpose(c: Call) -> str:
    """Deterministic purpose for backfill (no LLM): tool type is ground
    truth, then tool names, then transcript keywords.

    NOTE: linked order_numbers alone are NOT creation evidence — an
    order_status lookup touches an existing order (run 1678 looked up
    PB-49/PB-48) and must stay order_status. Only a create_order tool
    implies order_creation.
    """
    typed = _TYPE_TO_PURPOSE.get((c.call_type or "").strip(), "")
    if typed:
        return typed
    for t in c.tool_calls or []:
        if isinstance(t, dict):
            n = str(t.get("name", "") or t.get("tool", "")).lower()
            if "reserv" in n or "book" in n or "table" in n:
                return "reservation"
            if "status" in n or "track" in n or "lookup" in n:
                return "order_status"
            if "order" in n or "create" in n:
                return "order_creation"
    if c.order_numbers and (c.call_type or "").strip() == "create_order":
        return "order_creation"
    blob = " ".join(
        str((t.get("content") or t.get("text") or "")) if isinstance(t, dict) else str(t or "")
        for t in c.transcript or []
    ).lower()
    if blob:
        if any(k in blob for k in _RESERVATION_KW):
            return "reservation"
        if any(k in blob for k in _STATUS_KW):
            return "order_status"
    return ""


@router.post("/backfill-purposes", response_model=dict)
async def backfill_purposes(db: AsyncSession = Depends(get_db)):
    """Repair call_purpose on old rows.

    - Empty/"general" purposes are inferred (tool type → tool names →
      linked orders → transcript keywords).
    - Rows whose tool type disagrees with the stored purpose (e.g. an
      order_status call tagged order_creation from a bare order-number
      echo) are realigned to the tool type, which is ground truth.
    Returns {scanned, updated}.
    """
    _INTENT_NORM = {"order_status_check": "order_status",
                    "order_placement": "order_creation",
                    "status_check": "order_status"}
    calls = (await db.execute(select(Call))).scalars().all()
    updated = 0
    for c in calls:
        current = (c.call_purpose or "").strip()
        typed = _TYPE_TO_PURPOSE.get((c.call_type or "").strip(), "")
        if typed and current != typed:
            # Tool type wins — fixes echo-bug mistags and empty tags alike.
            c.call_purpose = typed
            updated += 1
        elif current in ("", "general"):
            inferred = _infer_call_purpose(c)
            if inferred and inferred != current:
                c.call_purpose = inferred
                updated += 1
        # Keep customer_intent in the same vocab and realign mistags
        # (run 1678: order_status call stored as order_creation).
        norm = _INTENT_NORM.get((c.customer_intent or "").strip(), None)
        if norm:
            c.customer_intent = norm
            updated += 1
        elif (c.customer_intent or "").strip() in ("order_creation", "order_status", "reservation") \
                and (c.call_purpose or "").strip() in ("order_creation", "order_status", "reservation") \
                and {c.customer_intent.strip(), c.call_purpose.strip()} == {"order_creation", "order_status"}:
            c.customer_intent = c.call_purpose.strip()
            updated += 1
        elif not (c.customer_intent or "").strip() and (c.call_purpose or "").strip():
            c.customer_intent = c.call_purpose.strip()[:64]
            updated += 1
    await db.commit()
    return {"scanned": len(calls), "updated": updated}
