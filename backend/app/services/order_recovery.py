"""Transcript-based order extraction via Google LLM (Gemini).

Runs on EVERY post-call webhook (not only on missing data):

1. Webhook saves the Call row + an order shell, then schedules this.
2. This loads the saved transcript (DB turns first, else transcript_url download).
3. Gemini returns STRICT JSON matching our order payload.
4. The same order shell is updated: empty fields filled, items replaced only
   when the shell has none (payload data is never clobbered by worse guesses).
5. If Gemini finds no order and the shell is still empty, the shell is removed
   so the dashboard stays clean.
"""

import json
import re
import secrets
from decimal import Decimal

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import SessionLocal
from app.models import Call, Customer, GoogleSetting, Memory, Order, OrderEvent, OrderItem
from app.schemas import OrderCreateRequest, clean_str
from app.services.embeddings import embed_one

settings = get_settings()

MAX_TRANSCRIPT_CHARS = 15000

DELIVERY_FEE = 2.50
FREE_DELIVERY_ABOVE = 25.00

# Order numbers: PB- + 3 phone-safe chars (kept in sync with routers/tools.py).
_ORDER_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _gen_order_number() -> str:
    return f"PB-{''.join(secrets.choice(_ORDER_CODE_ALPHABET) for _ in range(3))}"


def _extract_order_number_from_text(text: str) -> str:
    """Regex fallback when the LLM returns no order_number.

    Picks up PB-XXX codes and generic 'order (number|id|#) <code>' mentions
    straight from the transcript so we never invent an ID.
    """
    if not text:
        return ""
    m = re.search(r"\bPB-[A-Z0-9]{3,10}\b", text, re.IGNORECASE)
    if m:
        return m.group(0).upper().strip()
    m = re.search(
        r"order\s*(?:number|no\.?|id|#)?\s*(?:is|:)?\s*([A-Z0-9][A-Z0-9\-]{2,30})",
        text, re.IGNORECASE,
    )
    if m:
        cand = m.group(1).strip().strip(".,;:!?\"'").upper()
        if cand and cand not in {"IS", "THE", "A", "AN"}:
            return cand
    return ""

MENU = """PIZZAS (Small / Medium / Large) — crust Regular or Thin at no extra charge:
- Margherita Pizza (Veg): 8.99 / 11.99 / 14.99
- Pepperoni Pizza (Non-Veg): 9.99 / 12.99 / 15.99
- BBQ Chicken Pizza (Non-Veg): 10.99 / 13.99 / 16.99
- Veggie Supreme Pizza (Veg): 9.99 / 12.99 / 15.99
- Meat Lovers Pizza (Non-Veg): 11.99 / 14.99 / 17.99
- Four Cheese Pizza (Veg): 10.99 / 13.99 / 16.99
SIDES: Garlic Bread 4.99, Garlic Bread with Cheese 5.99, Chicken Wings (6 piece) 6.99, French Fries 3.49
DRINKS: Soft Drink can (Coke/Sprite) 1.99, Soft Drink 1.5 litre bottle 3.49, Bottled Water 1.49
DESSERTS: Chocolate Brownie 4.49, Choco Lava Cake 4.99"""

_PROMPT = """You extract a restaurant order from a phone-call transcript.
Return ONLY valid JSON, no markdown, no explanation, with exactly these keys:
{
  "order_number": "order ID / order number as spoken in the call (e.g. PB-ABC, ORD-123). Copy it EXACTLY as heard, else empty string — do NOT invent one",
  "customer_name": "full name or empty string",
  "customer_phone": "digits only, include + and country code if heard, else empty string",
  "order_type": "takeaway or dinein ONLY - delivery, pickup, takeaway and takeout all mean takeaway; use dinein only for eat-in/table service",
  "items": [{"name": "dish as heard", "quantity": number, "unit_price": number, "size": "Small/Medium/Large/Regular/Thin if mentioned else empty"}],
  "total": total number or 0,
  "delivery_address": "if mentioned else empty string",
  "payment_method": "cash/UPI/card if mentioned else empty string",
  "email": "if mentioned else empty string"
}
Prices (all in dollars): if the call quotes a price, use it. Otherwise use the MENU below,
matching item and size. Pizzas with no size heard default to Medium. Unknown items stay 0.
MENU:
""" + MENU + """
Rules: quantities default to 1. Do NOT add any delivery fee yourself — it is computed afterwards.
If no order was placed in the call, return {"no_order": true}.

Transcript:
{transcript}
"""


def _finalize_items(req, delivery_address: str) -> tuple[list[dict], float, bool]:
    """Items + total with server-side delivery fee.

    Subtotal comes from per-item prices (or the LLM total as fallback).
    A $2.50 delivery-fee line is appended when a delivery address exists and
    the subtotal is below $25. Returns (items, total, fee_applied).
    """
    items = [{
        "sku": (it.sku or "")[:128].strip(),
        "name": (it.name or it.sku or "Item")[:128].strip(),
        "quantity": it.quantity, "unit_price": float(it.unit_price or 0),
        "size": (it.size or "")[:32], "notes": (it.notes or "")[:256],
    } for it in req.items]
    subtotal = sum(i["unit_price"] * i["quantity"] for i in items)
    priced = any(i["unit_price"] > 0 for i in items)
    if not priced and req.total:
        subtotal = float(req.total)
    fee = bool((delivery_address or "").strip()) and subtotal < FREE_DELIVERY_ABOVE
    if fee:
        items.append({"sku": "", "name": "Delivery fee", "quantity": 1,
                      "unit_price": DELIVERY_FEE, "size": "", "notes": ""})
    return items, round(subtotal + (DELIVERY_FEE if fee else 0.0), 2), fee


async def _find_shell(db, call_id: str, order_number: str | None):
    """Locate the webhook's order shell: explicit number first, else the
    call's most recent order."""
    if order_number:
        o = (await db.execute(
            select(Order).options(selectinload(Order.items), selectinload(Order.events))
            .where(Order.order_number == order_number)
        )).scalar_one_or_none()
        if o:
            return o
    rows = (await db.execute(
        select(Order).options(selectinload(Order.items), selectinload(Order.events))
        .where(Order.call_id == call_id)
        .order_by(Order.id.desc()).limit(1)
    )).scalars().all()
    return rows[0] if rows else None


def _apply_extraction(order: Order, req: OrderCreateRequest, call: Call, shell_empty: bool) -> None:
    """Fill the shell from Gemini JSON. Empty shell fields are overwritten;
    items are replaced only when the shell has none (payload data wins)."""
    if not order.customer_name and (req.customer_name or "").strip():
        order.customer_name = req.customer_name.strip()
    if not order.customer_phone and (req.customer_phone or "").strip():
        order.customer_phone = req.customer_phone.strip()
    if not order.email and clean_str(req.email):
        order.email = clean_str(req.email)
    if not order.delivery_address and clean_str(req.delivery_address):
        order.delivery_address = clean_str(req.delivery_address)
    if not order.payment_method and clean_str(req.payment_method):
        order.payment_method = clean_str(req.payment_method)
    if (req.order_type or "") in ("takeaway", "dinein"):
        order.order_type = req.order_type
    # fall back to call-level info when Gemini heard nothing
    order.customer_name = order.customer_name or call.customer_name
    order.customer_phone = order.customer_phone or call.customer_phone

    if shell_empty and req.items:
        items, total, fee = _finalize_items(req, order.delivery_address)
        order.total = Decimal(str(total))
        for it in items:
            order.items.append(OrderItem(
                order_id=order.id, menu_item_id=None,
                sku=it["sku"], name=it["name"],
                quantity=it["quantity"], unit_price=Decimal(str(it["unit_price"])),
                size=it["size"], notes=it["notes"],
            ))
    order.source = "transcript_recovery"
    order.events.append(OrderEvent(order_id=order.id, status="confirmed",
                                   note="Order extracted from transcript via Google LLM"))


async def _get_google(db) -> GoogleSetting:
    row = (await db.execute(select(GoogleSetting).where(GoogleSetting.id == 1))).scalar_one_or_none()
    if not row:
        row = GoogleSetting(id=1, api_key="", model="gemini-2.0-flash")
        db.add(row)
        await db.flush()
    return row


def _transcript_text(call: Call) -> str:
    lines = []
    for t in call.transcript or []:
        if isinstance(t, dict) and t.get("content"):
            lines.append(f"{t.get('role', 'unknown')}: {t['content']}")
    return "\n".join(lines)


async def _download_text(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except Exception as e:
        logger.warning(f"order_recovery: transcript download failed: {e}")
        return ""
    ctype = resp.headers.get("content-type", "")
    body = resp.text
    if "json" in ctype or body.strip().startswith(("[", "{")):
        try:
            data = json.loads(body)
            return _text_from_json(data)
        except (json.JSONDecodeError, ValueError):
            pass
    return body


def _text_from_json(data) -> str:
    """Best-effort flatten of whatever transcript JSON shape Dograh serves."""
    if isinstance(data, str):
        return data
    if isinstance(data, list):
        parts = []
        for t in data:
            if isinstance(t, dict):
                c = t.get("content") or t.get("text") or ""
                if c:
                    parts.append(f"{t.get('role', t.get('speaker', 'unknown'))}: {c}")
            elif isinstance(t, str):
                parts.append(t)
        return "\n".join(parts)
    if isinstance(data, dict):
        for key in ("transcript", "text", "content", "turns", "messages"):
            if data.get(key):
                return _text_from_json(data[key])
        return json.dumps(data)[:MAX_TRANSCRIPT_CHARS]
    return str(data)[:MAX_TRANSCRIPT_CHARS]


async def _ask_gemini(transcript: str, api_key: str, model: str) -> dict | None:
    for attempt in ("extract", "repair"):
        prompt = _PROMPT.replace("{transcript}", transcript[:MAX_TRANSCRIPT_CHARS])
        if attempt == "repair":
            prompt += "\nYour previous reply was not valid JSON. Reply with JSON only."
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model}:generateContent?key={api_key}",
                    json={"contents": [{"parts": [{"text": prompt}]}],
                          "generationConfig": {"responseMimeType": "application/json"}},
                )
                resp.raise_for_status()
                parts = resp.json()["candidates"][0]["content"]["parts"]
                text = " ".join(p.get("text", "") for p in parts).strip()
        except Exception as e:
            logger.warning(f"order_recovery: Gemini call failed ({attempt}): {e}")
            return None
        parsed = _parse_json(text)
        if parsed is not None:
            return parsed
    return None


def _parse_json(text: str) -> dict | None:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t.lower().startswith("json"):
            t = t[4:].strip()
    try:
        data = json.loads(t)
    except (json.JSONDecodeError, ValueError):
        start, end = t.find("{"), t.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(t[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            return None
    return data if isinstance(data, dict) else None


async def extract_request_from_text(text: str, api_key: str, model: str):
    """One-shot: transcript text → validated OrderCreateRequest or None.
    Used synchronously by the webhook (straight path) and by the background
    job (fallback path)."""
    from app.schemas import OrderCreateRequest as _OCR
    data = await _ask_gemini(text, api_key, model or "gemini-2.0-flash")
    if not data or data.get("no_order"):
        return None
    try:
        req = _OCR.model_validate(data)
    except Exception as e:
        logger.warning(f"order_recovery: Gemini JSON failed validation: {e}")
        return None
    if not req.items:
        return None
    return req


async def get_google_key(db) -> tuple[str, str, bool]:
    """(api_key, model, enabled) — dashboard key first, env fallback."""
    g = await _get_google(db)
    api_key = (g.api_key or "").strip() or settings.gemini_api_key
    return api_key, (g.model or "gemini-2.0-flash"), bool(g.recovery_enabled)


async def create_order_from_transcript(
    db, text: str, call: Call, customer_name: str, customer_phone: str, call_id: str,
    note: str = "Order extracted from transcript via Google LLM",
):
    """Build a FULL order row straight from transcript text. Returns the
    Order or None (no transcript / no key / no order found / bad JSON).

    The order_number comes from the transcript (LLM-extracted, regex fallback)
    — it is NEVER generated here. Returns None when no order ID is heard.
    """
    from app.schemas import clean_str as _clean
    api_key, model, enabled = await get_google_key(db)
    if not enabled or not api_key or not (text or "").strip():
        return None
    req = await extract_request_from_text(text, api_key, model)
    if req is None:
        return None

    items, total, _fee = _finalize_items(req, _clean(req.delivery_address))

    order_number = (_clean(req.order_number) or "").strip().upper()
    if not order_number:
        order_number = _extract_order_number_from_text(text)
    if not order_number:
        logger.warning("order_recovery: no order_number in transcript, skipping save")
        return None

    # Reuse the row if this transcript order ID was already saved (idempotent).
    existing_order = (
        await db.execute(select(Order).where(Order.order_number == order_number))
    ).scalar_one_or_none()
    if existing_order is not None:
        logger.info(f"order_recovery: order {order_number!r} already exists, reusing")
        return existing_order

    order = Order(
        order_number=order_number,
        customer_name=(req.customer_name or "").strip() or customer_name,
        customer_phone=(req.customer_phone or "").strip() or customer_phone,
        email=_clean(req.email), order_type=req.order_type,
        table_number=_clean(req.table_number), pickup_time=_clean(req.pickup_time),
        delivery_address=_clean(req.delivery_address),
        payment_method=_clean(req.payment_method),
        status="confirmed", total=Decimal(str(total)),
        source="transcript_recovery", call_id=call_id,
    )
    db.add(order)
    await db.flush()
    for it in items:
        db.add(OrderItem(
            order_id=order.id, menu_item_id=None,
            sku=it["sku"], name=it["name"],
            quantity=it["quantity"], unit_price=Decimal(str(it["unit_price"])),
            size=it["size"], notes=it["notes"],
        ))
    db.add(OrderEvent(order_id=order.id, status="confirmed", note=note))
    if order.order_number not in (call.order_numbers or []):
        call.order_numbers = [*(call.order_numbers or []), order.order_number]
    return order


async def recover_order_from_call(call_id: str, order_number: str | None = None) -> dict:
    """Background entry point: extract one call's order from its transcript.
    Creates the order on success, saves nothing on failure — the UI only
    ever shows real, extracted orders."""
    async with SessionLocal() as db:
        call = (await db.execute(
            select(Call).where(Call.call_id == call_id)
        )).scalar_one_or_none()
        if not call:
            logger.warning(f"order_recovery: call {call_id!r} not found, skipping")
            return {"status": "skipped", "reason": "call not found"}

        g = await _get_google(db)
        api_key = (g.api_key or "").strip() or settings.gemini_api_key
        if not g.recovery_enabled:
            return {"status": "skipped", "reason": "recovery disabled"}
        if not api_key:
            logger.warning("order_recovery: no Google API key (dashboard or env), skipping")
            return {"status": "skipped", "reason": "no api key"}

        order = await _find_shell(db, call_id, order_number)
        shell_empty = (not order.items) if order else True

        text = _transcript_text(call)
        if not text and call.transcript_url:
            text = await _download_text(call.transcript_url)
        if not text.strip():
            return {"status": "skipped", "reason": "no transcript"}

        data = await _ask_gemini(text, api_key, g.model or "gemini-2.0-flash")
        if not data or data.get("no_order"):
            return {"status": "skipped", "reason": "no order in transcript"}

        try:
            req = OrderCreateRequest.model_validate(data)
        except Exception as e:
            logger.warning(f"order_recovery: Gemini JSON failed validation: {e}")
            return {"status": "failed", "reason": "validation"}
        if not req.items:
            return {"status": "skipped", "reason": "no items extracted"}

        if order:
            _apply_extraction(order, req, call, shell_empty)
        else:
            order = await create_order_from_transcript(
                db, text, call, call.customer_name, call.customer_phone, call_id)
            if order is None:  # pragma: no cover — defensive, already validated above
                return {"status": "failed", "reason": "create"}
        if order.order_number not in (call.order_numbers or []):
            call.order_numbers = [*(call.order_numbers or []), order.order_number]

        phone = order.customer_phone
        if phone:
            cust = (await db.execute(
                select(Customer).where(Customer.phone == phone))).scalar_one_or_none()
            if not cust:
                cust = Customer(phone=phone, name=order.customer_name)
                db.add(cust)
                await db.flush()
            cust.name = cust.name or order.customer_name
            vec = await embed_one(f"Recovered order {order.order_number} from call {call_id}")
            db.add(Memory(customer_id=cust.id, kind="order",
                          text=f"Placed order {order.order_number} (extracted from call transcript).",
                          embedding=vec))

        await db.commit()
        return {"status": "recovered", "order_number": order.order_number,
                "call_id": call_id}
