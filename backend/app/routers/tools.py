"""Tool endpoints called by the voice agent during live calls."""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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
    ctx_key_summary,
    is_unresolved_template,
)
from app.services.embeddings import embed_one

router = APIRouter(prefix="/api/tools", tags=["agent-tools"])


# Order numbers are sequential: PB-{total orders + 1} (first free code).
# Random codes are only a last-resort fallback that is practically unreachable.
_ORDER_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _gen_order_number() -> str:
    return f"PB-{''.join(secrets.choice(_ORDER_CODE_ALPHABET) for _ in range(3))}"


async def _gen_unique_order_number(db: AsyncSession) -> str:
    """First free PB-{n} starting at total orders + 1 (bumping past gaps)."""
    total = (await db.execute(select(func.count()).select_from(Order))).scalar_one() or 0
    candidate = (total or 0) + 1
    for _ in range(1000):
        num = f"PB-{candidate}"
        exists = (
            await db.execute(select(Order.id).where(Order.order_number == num))
        ).scalar_one_or_none()
        if exists is None:
            return num
        candidate += 1
    # Practically unreachable — fall back to a random code rather than failing.
    return f"PB-{''.join(secrets.choice(_ORDER_CODE_ALPHABET) for _ in range(6))}"


def _normalize_requested_number(raw: object) -> str:
    """Accept a pre-reserved number from the agent (PB-105 / PB-A1B shapes).

    Sequential PB-<digits> (from GET /api/orders/next-number) and legacy
    3-char phone-safe codes both pass; hallucinations ("P-B two four") fail.
    """
    import re as _re

    if not raw or not isinstance(raw, str):
        return ""
    s = raw.strip().upper().replace(" ", "").replace("_", "")
    m = _re.fullmatch(r"P-?B-?([A-Z0-9]{1,10})", s)
    if not m:
        return ""
    code = m.group(1)
    if code.isdigit():
        return f"PB-{code}"
    if len(code) < 3 or any(ch not in set(_ORDER_CODE_ALPHABET) for ch in code):
        return ""
    return f"PB-{code}"


async def _resolve_order_number(db: AsyncSession, requested: object) -> str:
    """Use the agent's pre-reserved number when it is valid and still free.

    This is what keeps transcript == DB: Dograh fetches next-number at call
    start, the agent speaks that PB-{n}, and passes the same value as
    `order_number` to create_order. Otherwise fall back to fresh PB-{total+1}.
    """
    want = _normalize_requested_number(requested)
    if want:
        exists = (
            await db.execute(select(Order.id).where(Order.order_number == want))
        ).scalar_one_or_none()
        if exists is None:
            return want
    return await _gen_unique_order_number(db)


# Context keys that may carry the provider / telephony / Genesys ids inside
# initial_context / gathered_context (Dograh run contexts). The Genesys
# CONVERSATION id is the Provider ID — never the session id; see run 1678:
#   session=c6608177-... conversation=81dabd1f-... provider=genesys
# Key groups are searched in order ACROSS all scopes, so a conversation id in
# gathered_context beats a session id in initial_context.
_CTX_PROVIDER_KEYS = ("provider", "telephony_provider", "call_provider")
_CTX_CONVERSATION_KEYS = ("conversation_id", "conversationId",
                          "genesys_conversation_id", "genesysConversationId")
_CTX_SESSION_KEYS = ("session_id", "sessionId", "correlation_id", "correlationId",
                     "genesys_session_id", "genesysSessionId",
                     "genesys_correlation_id", "genesysCorrelationId")
_CTX_OTHER_TELEPHONY_KEYS = (
    "provider_call_id", "telephony_id", "telephony_call_id", "call_sid",
    "telephony_call_sid", "twilio_sid", "twilio_call_sid", "vonage_uuid",
    "plivo_call_uuid", "telnyx_call_id", "call_uuid", "external_call_id",
    "provider_call_sid", "sip_call_id",
    "call_id",
)
_CTX_TELEPHONY_KEYS = (
    *_CTX_CONVERSATION_KEYS, *_CTX_OTHER_TELEPHONY_KEYS,
    *_CTX_SESSION_KEYS, "call_id",
)
_CTX_GENESYS_KEYS = (
    "conversation_id", "conversationId",
    "genesys_conversation_id", "genesysConversationId",
    "correlation_id", "correlationId", "session_id", "sessionId",
    "genesys_session_id", "genesysSessionId",
    "genesys_correlation_id", "genesysCorrelationId",
)


def _expand_scopes(scopes: list[dict]) -> list[dict]:
    """Top-level scopes plus one level of nested dicts.

    Dograh nests the Genesys ids (e.g. initial_context.audiohook =
    {conversationId, sessionId, ...} from the session-created log), so a
    flat key scan misses them. One extra level is enough and keeps the
    search deterministic.
    """
    out: list[dict] = []
    for scope in scopes:
        if not isinstance(scope, dict):
            continue
        out.append(scope)
        for v in scope.values():
            if isinstance(v, dict):
                out.append(v)
    return out


def _ctx_lookup(scopes: list[dict], keys: tuple[str, ...]) -> str:
    """First non-empty, non-template value for any key across scopes."""
    for scope in _expand_scopes(scopes):
        if not isinstance(scope, dict):
            continue
        lowered = {str(k).strip().lower(): v for k, v in scope.items()}
        for key in keys:
            for cand in (scope.get(key), lowered.get(key.lower())):
                if isinstance(cand, str) and cand.strip() and "{{" not in cand:
                    return cand.strip()
    return ""


def _normalize_intent(v: str) -> str:
    """Unify intent vocab to call_purpose values (order_creation|order_status|...)."""
    s = (v or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "order_placement": "order_creation",
        "create_order": "order_creation",
        "new_order": "order_creation",
        "place_order": "order_creation",
        "order": "order_creation",
        "order_status_check": "order_status",
        "status_check": "order_status",
        "track_order": "order_status",
        "table_booking": "reservation",
        "book_table": "reservation",
        "booking": "reservation",
    }
    s = aliases.get(s, s)
    return s if s in ("order_creation", "order_status", "reservation", "general") else ""


def _resolve_provider_from_contexts(
    provider: str, provider_call_id: str,
    initial_context: dict | None, gathered_context: dict | None,
) -> tuple[str, str, str]:
    """Fill provider / provider_call_id / genesys_conversation_id from contexts.

    The Dograh HTTP tools often send empty provider fields even though the
    run's initial_context carries {"provider": "genesys", "conversation_id":
    ...}. Without this the dashboard shows Provider=dograh and empty IDs.
    """
    scopes = [s for s in (initial_context or {}, gathered_context or {}) if isinstance(s, dict)]
    prov = (provider or "").strip().lower()
    if not prov or prov == "dograh":
        ctx_prov = _ctx_lookup(scopes, _CTX_PROVIDER_KEYS).lower()
        if ctx_prov:
            prov = ctx_prov[:32]
    prov = prov or "dograh"
    # Conversation id wins across ALL scopes before any session id is
    # considered (a session id in initial_context must not beat a
    # conversation id in gathered_context).
    conv = _ctx_lookup(scopes, _CTX_CONVERSATION_KEYS)
    pid = (provider_call_id or "").strip()
    if not pid:
        pid = (conv or _ctx_lookup(scopes, _CTX_OTHER_TELEPHONY_KEYS)
               or _ctx_lookup(scopes, _CTX_SESSION_KEYS)
               or _ctx_lookup(scopes, ("call_id",)))[:128]
    elif prov == "genesys" and conv and pid != conv:
        # Stored session id realigned to the conversation id.
        pid = conv
    genesys = (conv or _ctx_lookup(scopes, _CTX_GENESYS_KEYS))[:64]
    # Provider ID IS the conversation id: mirror both ways when Genesys.
    if prov == "genesys":
        if genesys and not pid:
            pid = genesys
        elif pid and not genesys:
            genesys = pid[:64]
        elif pid and genesys and pid != genesys:
            pid = genesys
    return prov[:32], pid[:128], genesys[:64]


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
    call_purpose: str = "",
    initial_context: dict | None = None,
    gathered_context: dict | None = None,
    customer_intent: str = "",
    genesys_conversation_id: str = "",
) -> Call | None:
    """Create or update the Call Logs row for this tool call.

    Every create_order / order_status / reservation call with a call_id lands
    here so the dashboard's Call Logs section shows one row per call with its
    type. `call_purpose` (order_creation | order_status | reservation |
    general) is the filter dimension — order_status / reservation calls never
    create an order row.
    """
    call_id = (call_id or "").strip()
    if not call_id:
        return None
    call_type = (call_type or "").strip()[:16]
    if not (call_purpose or "").strip():
        # Older callers only send call_type — derive the filter purpose.
        call_purpose = {"create_order": "order_creation",
                        "order_status": "order_status",
                        "reservation": "reservation"}.get((call_type or "").strip(), "")
    try:
        duration = int(duration or 0)
    except (ValueError, TypeError):
        duration = 0
    # Ground truth first: tool type decides purpose; incoming customer_intent
    # is only a hint and must never override a disagreeing purpose (e.g. an
    # order_status call tagged "order_creation" from a stale preset).
    call_purpose = _normalize_intent(call_purpose) or call_purpose
    norm_intent = _normalize_intent(customer_intent)
    if call_purpose in ("order_creation", "order_status", "reservation", "general"):
        if not norm_intent or (
            {norm_intent, call_purpose} == {"order_creation", "order_status"}
        ):
            norm_intent = call_purpose
    elif norm_intent:
        call_purpose = call_purpose or norm_intent
    provider, provider_call_id, _genesys = _resolve_provider_from_contexts(
        provider, provider_call_id, initial_context, gathered_context)
    # Explicit top-level Genesys id (Dograh preset mapped straight from the
    # session-created conversation id) beats anything inferred from contexts.
    _explicit_gen = (genesys_conversation_id or "").strip()
    if _explicit_gen:
        _genesys = _explicit_gen[:64]
        if provider == "dograh":
            provider = "genesys"
        if not provider_call_id or (provider == "genesys" and provider_call_id != _genesys):
            provider_call_id = _genesys[:128]
    call = (
        await db.execute(select(Call).where(Call.call_id == call_id))
    ).scalar_one_or_none()
    if call is None:
        call = Call(
            call_id=call_id,
            call_type=call_type,
            call_purpose=call_purpose or "",
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
            genesys_conversation_id=_genesys or "",
            initial_context=initial_context or {},
            gathered_context=gathered_context or {},
            customer_intent=(norm_intent or call_purpose or "")[:64],
        )
        db.add(call)
        try:
            await db.flush()
        except IntegrityError:
            # Lost the race with a concurrent tool call for the same call —
            # merge into the winner's row below instead of 500ing.
            await db.rollback()
            call = (
                await db.execute(select(Call).where(Call.call_id == call_id))
            ).scalar_one_or_none()
            if call is None:
                raise
        else:
            return call
    # update existing row with anything new the tool call carried
    if call_type and not call.call_type:
        call.call_type = call_type
    if call_purpose and (call.call_purpose or "").strip() in ("", "general"):
        call.call_purpose = call_purpose
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
    if initial_context:
        merged = dict(call.initial_context or {})
        merged.update(initial_context)
        call.initial_context = merged
    if gathered_context:
        merged = dict(call.gathered_context or {})
        merged.update(gathered_context)
        call.gathered_context = merged
    # Re-resolve against the merged contexts so a later call carrying the
    # Genesys ids backfills rows created by earlier id-less tool calls.
    _rprov, _rpid, _rgen = _resolve_provider_from_contexts(
        provider or call.provider or "", call.provider_call_id or provider_call_id or "",
        call.initial_context, call.gathered_context)
    if _rpid and not (call.provider_call_id or "").strip():
        call.provider_call_id = _rpid[:128]
    if _rprov and (not (call.provider or "").strip() or call.provider == "dograh"):
        call.provider = _rprov[:32]
    if _rgen and not (call.genesys_conversation_id or "").strip():
        call.genesys_conversation_id = _rgen[:64]
    if call.provider == "genesys" or _rprov == "genesys":
        call.provider = "genesys"
        # Session id stored earlier is realigned to the conversation id.
        if _rgen and (call.provider_call_id or "").strip() != _rgen:
            call.provider_call_id = _rgen[:128]
        if (call.provider_call_id or "").strip() \
                and not (call.genesys_conversation_id or "").strip():
            call.genesys_conversation_id = call.provider_call_id[:64]
    stored_intent = _normalize_intent(call.customer_intent or "")
    if call_purpose and (call.call_purpose or "").strip() in ("", "general"):
        call.call_purpose = call_purpose
    # Repair stale mistags: an order_status call stored as order_creation
    # (or vice versa) is realigned to the tool-type ground truth.
    if call_purpose in ("order_creation", "order_status", "reservation") and stored_intent in (
            "order_creation", "order_status", "reservation"):
        if {stored_intent, call_purpose} == {"order_creation", "order_status"}:
            call.customer_intent = call_purpose[:64]
            stored_intent = call_purpose
    if norm_intent and not stored_intent:
        call.customer_intent = norm_intent[:64]
    elif call_purpose and not (call.customer_intent or "").strip():
        call.customer_intent = call_purpose[:64]
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
        f"call_id={req.resolved_call_id()!r} "
        f"prov={req.resolved_provider()!r} pcall={req.resolved_provider_call_id()!r} "
        f"gen={req.resolved_genesys_id()!r} "
        f"ictx={ctx_key_summary(req.resolved_initial_context())} "
        f"gctx={ctx_key_summary(req.resolved_gathered_context())}"
    )
    if not req.customer_phone.strip() or not req.items:
        # Debug aid: log the raw top-level keys Dograh actually sent so an
        # empty-items pending call can be traced in api logs (wrong key name,
        # stringified array, {{...}} literal, ...).
        try:
            import json as _json

            raw_body = (await request.body()).decode("utf-8", errors="replace")
            raw_keys = sorted(_json.loads(raw_body).keys()) if raw_body.strip().startswith("{") else []
        except Exception:
            raw_keys = []
        missing = [f for f, ok in (
            ("customer_phone", bool(req.customer_phone.strip())),
            ("items", bool(req.items)),
        ) if not ok]
        logger.warning(
            f"POST /api/tools/orders PENDING (missing={missing}) "
            f"call_id={req.resolved_call_id()!r} raw_keys={raw_keys}"
        )
        await _log_tool_call(
            db,
            call_id=req.resolved_call_id(),
            call_type="create_order",
            call_purpose="order_creation",
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
            initial_context=req.resolved_initial_context(),
            gathered_context=req.resolved_gathered_context(),
            customer_intent=req.resolved_customer_intent() or "order_creation",
            genesys_conversation_id=req.resolved_genesys_id(),
        )
        await db.commit()
        return OrderCreatedResponse(
            order_number="", total=D("0"), status="pending", items=[],            message=(
                f"Order noted but incomplete (missing: {', '.join(missing)}). "
                "Do NOT read out any order number to the customer — none was "
                "created. Collect the missing details and call create_order "
                "again with the full items list."
            ),
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

    order_number = await _resolve_order_number(db, req.order_number)
    _ord_prov, _ord_pid, _ord_gen = _resolve_provider_from_contexts(
        req.resolved_provider(), req.resolved_provider_call_id(),
        req.resolved_initial_context(), req.resolved_gathered_context())
    _ord_gen = req.resolved_genesys_id() or _ord_gen
    if _ord_gen and _ord_prov == "dograh":
        _ord_prov = "genesys"
    if _ord_gen and not _ord_pid:
        _ord_pid = _ord_gen[:128]
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
        provider=_ord_prov,
        provider_call_id=_ord_pid,
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
        call_purpose="order_creation",
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
        initial_context=req.resolved_initial_context(),
        gathered_context=req.resolved_gathered_context(),
        customer_intent=req.resolved_customer_intent() or "order_placement",
        genesys_conversation_id=req.resolved_genesys_id(),
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

class OrderStatusLookupRequest(BaseModel):
    """POST body for order-status — Dograh-safe: order_number optional.

    Dograh templates often resolve {{order_number}} to null/empty when the
    caller hasn't said it yet. This model never 422s on null: every field
    has a default, stray-space keys + LLM aliases are mapped, and an empty
    order_number falls back to call_id / phone resolution.
    """

    model_config = {"extra": "ignore", "populate_by_name": True}

    order_number: Optional[Any] = ""
    call_id: Optional[Any] = ""
    workflow_run_id: Optional[Any] = ""
    customer_phone: Optional[Any] = ""
    phone: Optional[Any] = ""
    provider: Optional[Any] = ""
    provider_call_id: Optional[Any] = ""
    telephony_id: Optional[Any] = ""
    telephony_call_id: Optional[Any] = ""
    call_sid: Optional[Any] = ""
    genesys_conversation_id: Optional[Any] = ""
    first_name: Optional[Any] = ""
    rsvp: Optional[Any] = ""
    duration: Optional[Any] = 0
    duration_seconds: Optional[Any] = 0
    recording_url: Optional[Any] = ""
    user_recording_url: Optional[Any] = ""
    bot_recording_url: Optional[Any] = ""
    transcript_url: Optional[Any] = ""
    initial_context: Optional[Any] = None
    gathered_context: Optional[Any] = None
    customer_intent: Optional[Any] = ""

    @classmethod
    def _strip_keys(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        stripped: dict[str, Any] = {}
        for k, v in data.items():
            sk = str(k).strip()
            if sk not in stripped:
                stripped[sk] = v
        aliases = {
            "order number": "order_number",
            "order id": "order_number",
            "order_id": "order_number",
            "ordernumber": "order_number",
            "ordercode": "order_number",
            "order code": "order_number",
            "workflow run id": "call_id",
            "workflow_run_id": "call_id",
            "phone number": "customer_phone",
            "customer phone number": "customer_phone",
            "phone": "customer_phone",
            "telephony id": "provider_call_id",
            "telephony_id": "provider_call_id",
            "telephony call id": "provider_call_id",
            "provider call id": "provider_call_id",
            "provider_call_id": "provider_call_id",
            "provider id": "provider_call_id",
            "call sid": "provider_call_id",
            "first name": "first_name",
            "conversation_id": "genesys_conversation_id",
            "conversationid": "genesys_conversation_id",
            "conversation id": "genesys_conversation_id",
            "genesys_conversation_id": "genesys_conversation_id",
            "genesysconversationid": "genesys_conversation_id",
            "genesys conversation id": "genesys_conversation_id",
            "user recording url": "user_recording_url",
            "bot recording url": "bot_recording_url",
            "transcript url": "transcript_url",
        }
        normalized = {str(k).strip().lower().replace("-", " "): v for k, v in stripped.items()}
        for alias, canonical in aliases.items():
            if alias in normalized and not stripped.get(canonical):
                stripped[canonical] = normalized[alias]
        return stripped

    @model_validator(mode="before")  # type: ignore[misc]
    @classmethod
    def _map_aliases(cls, data: Any) -> Any:
        return cls._strip_keys(data)

    def _s(self, v: Any) -> str:
        return clean_str(v)

    def resolved_order_number(self) -> str:
        return self._s(self.order_number)

    def resolved_call_id(self) -> str:
        return self._s(self.call_id or self.workflow_run_id)

    def resolved_phone(self) -> str:
        return self._s(self.customer_phone or self.phone)

    def resolved_provider(self) -> str:
        p = self._s(self.provider).lower() or "dograh"
        return p[:32]

    def resolved_provider_call_id(self) -> str:
        for cand in (self.provider_call_id, self.telephony_id,
                      self.telephony_call_id, self.call_sid):
            s = self._s(cand)
            if s:
                return s[:128]
        return ""

    def resolved_genesys_id(self) -> str:
        return self._s(self.genesys_conversation_id)[:64]

    def resolved_duration(self) -> int:
        return _coerce_int(self.duration_seconds or self.duration)

    def resolved_first_name(self) -> str:
        return self._s(self.first_name)

    def _ctx(self, v: Any) -> dict:
        if isinstance(v, dict):
            return v
        if isinstance(v, str) and v.strip():
            import json as _json
            try:
                p = _json.loads(v.strip())
                return p if isinstance(p, dict) else {}
            except Exception:
                return {}
        return {}

    def resolved_initial_context(self) -> dict:
        return self._ctx(self.initial_context)

    def resolved_gathered_context(self) -> dict:
        return self._ctx(self.gathered_context)

    def resolved_customer_intent(self) -> str:
        # Canonical vocab is order_status (not order_status_check); normalize
        # legacy values so the badge matches call_purpose.
        s = self._s(self.customer_intent).lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "order_status_check": "order_status",
            "status_check": "order_status",
            "order_placement": "order_creation",
            "create_order": "order_creation",
        }
        s = aliases.get(s, s)
        if s not in ("order_creation", "order_status", "reservation", "general"):
            s = "order_status" if not s else s
            if s not in ("order_creation", "order_status", "reservation", "general"):
                s = "order_status"
        return s[:64]


async def _resolve_order_for_status(
    db: AsyncSession, requested: str, call_id: str, phone: str
) -> tuple[Any | None, str, str]:
    """Return (order|None, effective_number, lookup_method).

    Methods: exact | via_call_id | via_call_log | via_phone | none.
    Phone matching is country-code insensitive: callers speak 10 digits
    ("9967835090") while rows may store "+919967835090" — digit suffixes
    are compared instead of raw strings.
    """
    requested = (requested or "").strip()
    if requested:
        order = (
            await db.execute(
                select(Order)
                .options(selectinload(Order.items), selectinload(Order.events))
                .where(func.upper(Order.order_number) == requested.upper())
            )
        ).scalar_one_or_none()
        if order:
            return order, order.order_number, "exact"
    # Fallback 1: latest order row stamped with this call_id.
    if call_id:
        o = (
            await db.execute(
                select(Order)
                .options(selectinload(Order.items), selectinload(Order.events))
                .where(Order.call_id == call_id)
                .order_by(Order.id.desc())
                .limit(1)
            )
        ).scalars().first()
        if o:
            return o, o.order_number, "via_call_id"
        # Fallback 2: Call Logs row for this call carries order_numbers.
        c = (
            await db.execute(select(Call).where(Call.call_id == call_id))
        ).scalar_one_or_none()
        if c and c.order_numbers:
            last = str((c.order_numbers or [])[-1]).strip()
            if last:
                o2 = (
                    await db.execute(
                        select(Order)
                        .options(selectinload(Order.items), selectinload(Order.events))
                        .where(func.upper(Order.order_number) == last.upper())
                    )
                ).scalar_one_or_none()
                if o2:
                    return o2, o2.order_number, "via_call_log"
    # Fallback 3: latest order for this phone (country-code insensitive).
    if phone and _digits(phone):
        dd = _digits(phone)
        cands: list[Order] = []
        if len(dd) >= 7:
            # DB prefilter on the trailing digits, then exact suffix match in
            # Python (handles +91/+1 prefixes callers never speak).
            tail = dd[-7:]
            cands = (await db.execute(
                select(Order)
                .options(selectinload(Order.items), selectinload(Order.events))
                .where(Order.customer_phone.ilike(f"%{tail}%"))
                .order_by(Order.id.desc())
                .limit(50)
            )).scalars().all()
        else:
            cands = (await db.execute(
                select(Order)
                .options(selectinload(Order.items), selectinload(Order.events))
                .where(Order.customer_phone == phone.strip())
                .order_by(Order.id.desc())
                .limit(1)
            )).scalars().all()
        for o3 in cands:
            if _phones_match(o3.customer_phone, phone):
                return o3, o3.order_number, "via_phone"
    return None, requested, "none"


def _digits(s: object) -> str:
    """Phone digits only: "+91 99678 35090" → "919967835090"."""
    import re as _re
    return _re.sub(r"\D", "", str(s or ""))


def _phones_match(a: object, b: object) -> bool:
    """Country-code-insensitive phone equality.

    "9967835090" matches "+919967835090" (suffix), "+1..." US numbers match
    their 10-digit spoken form. Short fragments (<7 digits) require exact
    equality to avoid junk matches.
    """
    da, db = _digits(a), _digits(b)
    if not da or not db:
        return False
    if len(da) < 7 or len(db) < 7:
        return da == db
    return da.endswith(db) or db.endswith(da)


@router.post("/order-status", response_model=dict)
async def order_status_post(req: OrderStatusLookupRequest, db: AsyncSession = Depends(get_db)):
    """POST variant — Dograh sends {order_number, call_id, ...} as JSON body.

    Configure in Dograh as HTTP API tool: POST <BASE>/api/tools/order-status
    with params order_number (LLM), call_id preset {{workflow_run_id}},
    customer_phone preset, provider/provider_call_id presets, etc.
    When order_number arrives null/empty/unresolved, the endpoint resolves
    the correct order via call_id (order row / call log) or phone instead
    of returning not-found — then logs a Call Logs row of type order_status.
    """
    requested = req.resolved_order_number()
    call_id = req.resolved_call_id()
    phone = req.resolved_phone()
    order, effective, method = await _resolve_order_for_status(db, requested, call_id, phone)
    await _log_tool_call(
        db,
        call_id=call_id,
        call_type="order_status",
        call_purpose="order_status",
        first_name=req.resolved_first_name(),
        rsvp=clean_str(req.rsvp),
        duration=req.resolved_duration(),
        recording_url=clean_str(req.recording_url),
        user_recording_url=clean_str(req.user_recording_url),
        bot_recording_url=clean_str(req.bot_recording_url),
        transcript_url=clean_str(req.transcript_url),
        order_number=effective,
        tool_name="order_status",
        tool_summary=effective or requested or "(no order number)",
        customer_name=order.customer_name if order else "",
        customer_phone=order.customer_phone if order else phone,
        provider=req.resolved_provider(),
        provider_call_id=req.resolved_provider_call_id(),
        initial_context=req.resolved_initial_context(),
        gathered_context=req.resolved_gathered_context(),
        customer_intent=req.resolved_customer_intent(),
        genesys_conversation_id=req.resolved_genesys_id(),
    )
    if order is not None and order.customer_phone:
        _cust = (await db.execute(
            select(Customer).where(Customer.phone == order.customer_phone)
        )).scalar_one_or_none()
        if _cust is not None and not (_cust.last_intent or "").strip():
            _cust.last_intent = "order_status"
    await db.commit()
    if order is None:
        hint = (
            "No order number was provided and none could be resolved from call_id/phone. "
            "Ask the customer for their order number (e.g. PB-A1B) or phone number."
            if not requested else f"No order found for {requested}"
        )
        return {"found": False, "message": hint,
                "requested_order_number": requested, "resolved_order_number": "",
                "lookup_method": method}
    payload = OrderStatusResponse(found=True, order=order)  # type: ignore[arg-type]
    data = payload.model_dump()
    data["requested_order_number"] = requested
    data["resolved_order_number"] = effective
    data["lookup_method"] = method
    data["matched_phone"] = order.customer_phone if order else ""
    return data


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
        call_purpose="order_status",
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
    if result.found and result.order and result.order.customer_phone:
        # Tag the looked-up customer's intent for the memory endpoint.
        _cust = (await db.execute(
            select(Customer).where(Customer.phone == result.order.customer_phone)
        )).scalar_one_or_none()
        if _cust is not None and not (_cust.last_intent or "").strip():
            _cust.last_intent = "order_status_check"
    await db.commit()
    return result


@router.get("/order-status", response_model=dict)
async def order_status_query(
    order_number: Optional[str] = Query(default="", description="Order number, e.g. PB-1382"),
    phone: Optional[str] = Query(default="", description="Customer phone digits (country code optional) — returns the latest order for it"),
    customer_phone: Optional[str] = Query(default="", description="Alias of phone"),
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
    """Query-param variant — friendlier for tool platforms without path params.

    URL structures:
    - by number: /api/tools/order-status?order_number=PB-1382
    - by phone (latest order, country code optional): /api/tools/order-status?phone=9967835090
    - either + call logging: ...&call_id={{workflow_run_id}}
    """
    requested = clean_str(order_number)
    ph = clean_str(customer_phone or phone)
    order, effective, method = await _resolve_order_for_status(
        db, requested, clean_str(call_id), ph)
    await _log_tool_call(
        db,
        call_id=clean_str(call_id),
        call_type="order_status",
        call_purpose="order_status",
        first_name=clean_str(first_name),
        rsvp=clean_str(rsvp),
        duration=_coerce_int(duration),
        recording_url=clean_str(recording_url),
        user_recording_url=clean_str(user_recording_url),
        bot_recording_url=clean_str(bot_recording_url),
        transcript_url=clean_str(transcript_url),
        order_number=effective,
        tool_name="order_status",
        tool_summary=effective or requested or ph or "(no order number)",
        customer_name=order.customer_name if order else "",
        customer_phone=order.customer_phone if order else ph,
    )
    if order is not None and order.customer_phone:
        _cust = (await db.execute(
            select(Customer).where(Customer.phone == order.customer_phone)
        )).scalar_one_or_none()
        if _cust is not None and not (_cust.last_intent or "").strip():
            _cust.last_intent = "order_status"
    await db.commit()
    if order is None:
        hint = (
            "No order number was provided and none could be resolved from call_id/phone. "
            "Ask the customer for their order number (e.g. PB-1382) or phone number."
            if not requested else f"No order found for {requested}"
        )
        return {"found": False, "message": hint,
                "requested_order_number": requested, "resolved_order_number": "",
                "lookup_method": method, "matched_phone": ""}
    payload = OrderStatusResponse(found=True, order=order)  # type: ignore[arg-type]
    data = payload.model_dump()
    data["requested_order_number"] = requested
    data["resolved_order_number"] = effective
    data["lookup_method"] = method
    data["matched_phone"] = order.customer_phone
    return data


# --------------------------------------------------------------- reservation

class ReservationLogRequest(BaseModel):
    call_id: str = ""
    workflow_run_id: str = ""
    customer_name: str = ""
    customer_phone: str = ""
    first_name: str = ""
    rsvp: str = ""
    party_size: Optional[int] = None
    reservation_date: str = ""
    reservation_time: str = ""
    duration: Optional[Any] = 0
    duration_seconds: Optional[Any] = 0
    recording_url: str = ""
    user_recording_url: str = ""
    bot_recording_url: str = ""
    transcript_url: str = ""
    provider: str = ""
    provider_call_id: str = ""
    telephony_id: str = ""
    genesys_conversation_id: str = ""
    initial_context: dict | None = None
    gathered_context: dict | None = None
    customer_intent: str = ""


@router.post("/reservations", status_code=201)
async def log_reservation(req: ReservationLogRequest, db: AsyncSession = Depends(get_db)):
    """Log a reservation / table-booking call. Creates NO order row — it only
    tags the Call with purpose=reservation so it can be filtered separately
    from order_creation and order_status calls."""
    from app.schemas import OrderCreateRequest as _OCR

    call_id = (req.call_id or req.workflow_run_id or "").strip()
    # Reuse OrderCreateRequest resolvers for provider/duration cleaning.
    tmp = _OCR.model_validate({
        "call_id": call_id, "first_name": req.first_name, "rsvp": req.rsvp,
        "duration": req.duration, "duration_seconds": req.duration_seconds,
        "recording_url": req.recording_url,
        "user_recording_url": req.user_recording_url,
        "bot_recording_url": req.bot_recording_url,
        "transcript_url": req.transcript_url, "provider": req.provider,
        "provider_call_id": req.provider_call_id, "telephony_id": req.telephony_id,
    })
    bits = []
    if req.party_size:
        bits.append(f"party of {req.party_size}")
    if req.reservation_date:
        bits.append(req.reservation_date)
    if req.reservation_time:
        bits.append(req.reservation_time)
    summary = f"Reservation {' '.join(bits)}".strip() or "Reservation noted"
    await _log_tool_call(
        db, call_id=call_id, call_type="reservation",
        call_purpose="reservation",
        customer_name=clean_str(req.customer_name),
        customer_phone=clean_str(req.customer_phone),
        first_name=tmp.resolved_first_name(), rsvp=clean_str(req.rsvp),
        duration=tmp.resolved_duration(),
        recording_url=clean_str(req.recording_url),
        user_recording_url=clean_str(req.user_recording_url),
        bot_recording_url=clean_str(req.bot_recording_url),
        transcript_url=clean_str(req.transcript_url),
        tool_name="reservation", tool_summary=summary,
        provider=tmp.resolved_provider(),
        provider_call_id=tmp.resolved_provider_call_id(),
        initial_context=req.initial_context or {},
        gathered_context=req.gathered_context or {},
        customer_intent=(req.customer_intent or "reservation")[:64],
        genesys_conversation_id=clean_str(req.genesys_conversation_id),
    )
    _phone = clean_str(req.customer_phone)
    if _phone:
        _cust = (await db.execute(
            select(Customer).where(Customer.phone == _phone))).scalar_one_or_none()
        if _cust is None:
            _cust = Customer(phone=_phone, name=clean_str(req.customer_name))
            db.add(_cust)
            await db.flush()
        _cust.last_intent = "reservation"
        await _add_memory(db, _cust, "note", f"Reservation call: {summary}.")
    await db.commit()
    return {"status": "recorded", "call_id": call_id, "call_purpose": "reservation"}


def _coerce_int(v: object) -> int:
    if v in (None, ""):
        return 0
    try:
        return int(float(str(v)))
    except (ValueError, TypeError):
        return 0


# --------------------------------------------------------------- dummy log
# (input capture without ordering — for setups not using create_order)

class LogCallRequest(BaseModel):
    """Dummy/log-only tool call: records inputs on the Call Logs row and
    creates NO order row. Dograh-safe: every field optional, stray-space keys
    + common aliases mapped, {{...}} unresolved literals tolerated.

    Configure in Dograh as HTTP API tool: POST <BASE>/api/tools/log-call.
    The response echoes the resolved provider chain so you can verify the
    Genesys conversation id mapping straight from the Dograh response.
    """

    model_config = {"extra": "ignore", "populate_by_name": True}

    call_id: Optional[Any] = ""
    workflow_run_id: Optional[Any] = ""
    tool_name: Optional[Any] = "capture"
    inputs: Optional[Any] = None
    customer_name: Optional[Any] = ""
    customer_phone: Optional[Any] = ""
    phone: Optional[Any] = ""
    first_name: Optional[Any] = ""
    rsvp: Optional[Any] = ""
    duration: Optional[Any] = 0
    duration_seconds: Optional[Any] = 0
    recording_url: Optional[Any] = ""
    user_recording_url: Optional[Any] = ""
    bot_recording_url: Optional[Any] = ""
    transcript_url: Optional[Any] = ""
    provider: Optional[Any] = ""
    provider_call_id: Optional[Any] = ""
    telephony_id: Optional[Any] = ""
    genesys_conversation_id: Optional[Any] = ""
    initial_context: Optional[Any] = None
    gathered_context: Optional[Any] = None
    customer_intent: Optional[Any] = ""
    call_purpose: Optional[Any] = ""
    # Same order-context fields create_order accepts, so log-call captures
    # the identical context without creating an order row. Values merge into
    # gathered_context; order_number also links onto the call's order list.
    email: Optional[Any] = ""
    order_number: Optional[Any] = ""
    order_type: Optional[Any] = ""
    table_number: Optional[Any] = ""
    pickup_time: Optional[Any] = ""
    delivery_address: Optional[Any] = ""
    payment_method: Optional[Any] = ""

    @model_validator(mode="before")  # type: ignore[misc]
    @classmethod
    def _map_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        stripped: dict[str, Any] = {}
        for k, v in data.items():
            sk = str(k).strip()
            if sk not in stripped:
                stripped[sk] = v
        aliases = {
            "workflow run id": "call_id",
            "workflow_run_id": "call_id",
            "order number": "order_number",
            "order id": "order_number",
            "ordernumber": "order_number",
            "fulfillment type": "order_type",
            "fulfillment_type": "order_type",
            "order type": "order_type",
            "table": "table_number",
            "pickup time": "pickup_time",
            "delivery address": "delivery_address",
            "payment method": "payment_method",
            "tool name": "tool_name",
            "toolname": "tool_name",
            "input": "inputs",
            "args": "inputs",
            "arguments": "inputs",
            "data": "inputs",
            "params": "inputs",
            "parameters": "inputs",
            "phone number": "customer_phone",
            "customer phone number": "customer_phone",
            "phone": "customer_phone",
            "customer name": "customer_name",
            "name": "customer_name",
            "first name": "first_name",
            "telephony id": "provider_call_id",
            "telephony_id": "provider_call_id",
            "provider call id": "provider_call_id",
            "provider_call_id": "provider_call_id",
            "provider id": "provider_call_id",
            "call sid": "provider_call_id",
            "conversation_id": "genesys_conversation_id",
            "conversationid": "genesys_conversation_id",
            "conversation id": "genesys_conversation_id",
            "genesys_conversation_id": "genesys_conversation_id",
            "genesysconversationid": "genesys_conversation_id",
            "genesys conversation id": "genesys_conversation_id",
            "user recording url": "user_recording_url",
            "bot recording url": "bot_recording_url",
            "transcript url": "transcript_url",
        }
        normalized = {str(k).strip().lower().replace("-", " "): v for k, v in stripped.items()}
        for alias, canonical in aliases.items():
            if alias in normalized and not stripped.get(canonical):
                stripped[canonical] = normalized[alias]
        return stripped

    def _s(self, v: Any) -> str:
        return clean_str(v)

    def resolved_call_id(self) -> str:
        return self._s(self.call_id or self.workflow_run_id)

    def resolved_tool_name(self) -> str:
        return self._s(self.tool_name)[:64] or "capture"

    def resolved_inputs_summary(self) -> str:
        ins = self.inputs
        if ins in (None, "") or is_unresolved_template(ins):
            return ""
        if isinstance(ins, dict):
            bits = [f"{k}={v}" for k, v in list(ins.items())[:12]]
            s = ", ".join(bits)
        elif isinstance(ins, list):
            s = ", ".join(str(x) for x in ins[:12])
        else:
            s = str(ins)
        return s[:500]

    def _ctx(self, v: Any) -> dict:
        if isinstance(v, dict):
            return v
        if isinstance(v, str) and v.strip():
            import json as _json
            try:
                p = _json.loads(v.strip())
                return p if isinstance(p, dict) else {}
            except Exception:
                return {}
        return {}


@router.post("/log-call", response_model=dict, status_code=201)
async def log_tool_call(req: LogCallRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Log-only tool call: stores inputs on the Call Logs row, no order row.

    For setups not using create_order — lets you see exactly what inputs the
    agent gathered (tool name + args summary + contexts + provider chain) in
    Transactions → Call Logs. Never 422s: missing call_id just returns
    logged=False with a message instead of failing the call.
    """
    call_id = req.resolved_call_id()
    if not call_id or is_unresolved_template(call_id):
        return {"status": "skipped", "logged": False, "call_id": "",
                "message": "No call_id (preset {{workflow_run_id}}) — nothing was logged. "
                           "Add call_id as a preset parameter with value {{workflow_run_id}}."}
    tool_name = req.resolved_tool_name()
    summary = req.resolved_inputs_summary() or "(no inputs)"
    # create_order parity: fold the order-context fields into gathered_context
    # so log-call captures the identical context (email, fulfillment, table,
    # pickup, address, payment) without creating an order row.
    _gctx = req._ctx(req.gathered_context)
    for _k, _v in (
        ("email", req._s(req.email)),
        ("order_number", req._s(req.order_number)),
        ("order_type", req._s(req.order_type)),
        ("table_number", req._s(req.table_number)),
        ("pickup_time", req._s(req.pickup_time)),
        ("delivery_address", req._s(req.delivery_address)),
        ("payment_method", req._s(req.payment_method)),
    ):
        if _v and _k not in _gctx:
            _gctx[_k] = _v
    logger.info(
        f"POST /api/tools/log-call from {request.client.host if request.client else '?'} "
        f"| call_id={call_id!r} tool={tool_name!r} inputs={summary[:200]!r} "
        f"prov={req._s(req.provider)!r} pcall={clean_str(req.provider_call_id or req.telephony_id)!r} "
        f"gen={req._s(req.genesys_conversation_id)!r} "
        f"ictx={ctx_key_summary(req._ctx(req.initial_context))} "
        f"gctx={ctx_key_summary(_gctx)}"
    )
    call = await _log_tool_call(
        db,
        call_id=call_id,
        call_type="tool_log",
        call_purpose=req._s(req.call_purpose),
        customer_name=req._s(req.customer_name),
        customer_phone=req._s(req.customer_phone or req.phone),
        first_name=req._s(req.first_name),
        rsvp=clean_str(req.rsvp),
        duration=_coerce_int(req.duration_seconds or req.duration),
        recording_url=clean_str(req.recording_url),
        user_recording_url=clean_str(req.user_recording_url),
        bot_recording_url=clean_str(req.bot_recording_url),
        transcript_url=clean_str(req.transcript_url),
        order_number=req._s(req.order_number),
        tool_name=tool_name,
        tool_summary=summary,
        provider=req._s(req.provider),
        provider_call_id=clean_str(req.provider_call_id or req.telephony_id),
        initial_context=req._ctx(req.initial_context),
        gathered_context=_gctx,
        customer_intent=req._s(req.customer_intent),
        genesys_conversation_id=req._s(req.genesys_conversation_id),
    )
    await db.commit()
    return {
        "status": "logged",
        "logged": True,
        "call_id": call_id,
        "tool": tool_name,
        "provider": (call.provider if call else "") or "",
        "provider_call_id": (call.provider_call_id if call else "") or "",
        "genesys_conversation_id": (call.genesys_conversation_id if call else "") or "",
        "message": f"Inputs recorded for call {call_id} — no order created.",
    }


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
