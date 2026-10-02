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
from app.models import Call, Customer, DograhSetting, GoogleSetting, Memory, Order, OrderEvent, OrderItem
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


def _valid_order_number(raw: object) -> str:
    """Accept only real PB-style codes.

    Two shapes pass: sequential numeric codes (PB-105 — what the agent
    reads out from next-number) and 3–10 phone-safe chars (PB-A1B).
    The voice agent sometimes hallucinates a number ("P-B two four") when a
    tool call returns empty — word-codes fail the alphabet check and are
    rejected, so that must never become a stored order number. Normalizes
    separators/case ("pb 105" → "PB-105"); anything else → "".
    """
    if not raw or not isinstance(raw, str):
        return ""
    # Strip every separator first: the agent reads digit-by-digit
    # ("P-B-1-0-5"), customers say "PB 105" — all normalize to PB105.
    s = re.sub(r"[\s\-_]", "", raw.strip().upper())
    m = re.fullmatch(r"PB([A-Z0-9]{1,10})", s)
    if not m:
        return ""
    code = m.group(1)
    if code.isdigit():
        return f"PB-{code}"
    if len(code) < 3 or any(ch not in _ORDER_CODE_SET for ch in code):
        return ""
    return f"PB-{code}"


_ORDER_CODE_SET = set(_ORDER_CODE_ALPHABET)


def _plausible_order_number(raw: object) -> str:
    """Loose shape check: real order numbers never contain spaces.

    Used for missing-field computation and legacy (KFC-hex) codes. An
    agent-hallucinated number ("P-B two four") always contains spaces and
    is rejected here; strict PB validation + DB existence still decide
    what actually gets stored.
    """
    if not raw or not isinstance(raw, str):
        return ""
    s = raw.strip().upper()
    if not s or " " in s or len(s) > 16:
        return ""
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9\-]{2,15}", s):
        return ""
    return s


def _extract_order_number_from_text(text: str) -> str:
    """Strict PB-code fallback when the LLM returns no order_number.

    Only true PB-style codes are accepted so an agent-hallucinated number
    spoken in the transcript ("P-B two four") never becomes a stored order.
    """
    if not text:
        return ""
    # Digit runs first ("P-B-1-0-5", "PB 105"): separators between digits
    # belong to the code, so "PB-1" partials can never match.
    for m in re.finditer(
        r"\bP\s?-?\s?B\s?-?\s?(\d(?:\s?-?\s?\d){0,9})\b", text, re.IGNORECASE
    ):
        cand = _valid_order_number("PB-" + m.group(1))
        if cand:
            return cand
    # Letter codes ("PB-A1B"): contiguous, so trailing words
    # ("PB-A1B confirmed") can't poison the match.
    for m in re.finditer(
        r"\bP\s?-?\s?B\s?-?\s?([A-Z0-9]{3,10})\b", text, re.IGNORECASE
    ):
        cand = _valid_order_number("PB-" + m.group(1))
        if cand:
            return cand
    return ""

def _extract_phone_from_text(text: str) -> str:
    """Last-resort phone: callers rarely speak their number, but when they do
    (or the transcript carries ANI/CLI headers), grab it so the order and its
    transaction still carry customer details."""
    if not text:
        return ""
    cands: list[str] = []
    for m in re.finditer(r"\+?\d[\d\s\-().]{7,}\d", text):
        digits = re.sub(r"\D", "", m.group(0))
        if 10 <= len(digits) <= 15:
            cands.append(("+" + digits) if m.group(0).strip().startswith("+") else digits)
    if not cands:
        return ""
    for c in cands:  # prefer international format
        if c.startswith("+"):
            return c
    return sorted(cands, key=len, reverse=True)[0]


_FALLBACK_MODEL = "gemini-3.5-flash"

# Call-purpose values used for dashboard filtering. order-status and
# reservation calls NEVER create an order row — the purpose is tagged on the
# Call so they can still be listed / filtered separately.
CALL_PURPOSES = ("order_creation", "order_status", "reservation", "general")
_NON_ORDER_PURPOSES = ("order_status", "reservation")
INCOMPLETE_STATUS = "incomplete"


def _normalize_purpose(raw: object, data: dict | None = None, text: str = "",
                     order_json: bool = False) -> str:
    """LLM purpose → canonical value, with keyword fallback.

    When the model returned an order JSON (order_json=True) but omitted the
    purpose, it is an order_creation attempt by construction — never "general".
    """
    s = (str(raw or "").strip().lower().replace("-", "_").replace(" ", "_"))
    aliases = {
        "create_order": "order_creation", "new_order": "order_creation",
        "place_order": "order_creation", "order": "order_creation",
        "order_status_check": "order_status", "status_check": "order_status",
        "track_order": "order_status", "table_booking": "reservation",
        "book_table": "reservation", "booking": "reservation",
    }
    s = aliases.get(s, s)
    if s in CALL_PURPOSES:
        return s
    # No explicit purpose → infer from reason + transcript text.
    blob = f"{((data or {}).get('reason') or '')} {text or ''}".lower()
    if any(k in blob for k in ("reserv", "book a table", "party size", "table for")):
        return "reservation"
    if any(k in blob for k in ("order status", "status of my order", "status check",
                               "where is my order", "is my order", "track my order",
                               "cancel my order", "how long for my order")):
        return "order_status"
    if order_json:
        # An order payload IS an order attempt (even partial) — the prompt
        # reserves no_order for status/reservation/general.
        return "order_creation"
    return "general"


def _compute_missing(req, text: str) -> list[str]:
    """Fields absent from an order_creation attempt → saved as incomplete."""
    missing: list[str] = []
    if not (req.customer_name or "").strip():
        missing.append("customer_name")
    if not (req.customer_phone or "").strip():
        missing.append("customer_phone")
    if not req.items:
        missing.append("items")
    number = _valid_order_number(clean_str(req.order_number)) \
        or _extract_order_number_from_text(text or "") \
        or _plausible_order_number(clean_str(req.order_number))
    if not number:
        missing.append("order_number")
    if not req.order_confirmed:
        missing.append("confirmation")
    for extra in (req.missing_fields or []):
        e = str(extra).strip()
        if e and e not in missing:
            missing.append(e)
    return missing


def _redact_key(msg: str) -> str:
    """Strip secrets from log lines (delegates to the shared Gemini helper)."""
    from app.services.gemini import redact
    return redact(msg)


MENU = """MENU (hardcoded — never invent an item, size, or price not listed here)
PIZZAS (Small / Medium / Large):
1. Margherita Pizza (Veg) — mozzarella, tomato sauce, basil — $8.99 / $11.99 / $14.99
2. Pepperoni Pizza (Non-Veg) — pepperoni, mozzarella, tomato sauce — $9.99 / $12.99 / $15.99
3. BBQ Chicken Pizza (Non-Veg) — grilled chicken, BBQ sauce, red onion, mozzarella — $10.99 / $13.99 / $16.99
4. Veggie Supreme Pizza (Veg) — bell pepper, onion, mushroom, olives, sweetcorn — $9.99 / $12.99 / $15.99
5. Meat Lovers Pizza (Non-Veg) — pepperoni, sausage, bacon, ham — $11.99 / $14.99 / $17.99
6. Four Cheese Pizza (Veg) — mozzarella, cheddar, parmesan, gorgonzola — $10.99 / $13.99 / $16.99
Crust: Regular or Thin, available on any pizza at no extra charge.

SIDES:
- Garlic Bread — $4.99
- Garlic Bread with Cheese — $5.99
- Chicken Wings (6 piece) — $6.99
- French Fries — $3.49

DRINKS:
- Soft Drink can (Coke / Sprite) — $1.99
- Soft Drink 1.5 litre bottle — $3.49
- Bottled Water — $1.49

DESSERTS:
- Chocolate Brownie — $4.49
- Choco Lava Cake — $4.99

Name aliases customers use: "Veg Supreme" / "Veggie Supreme" = Veggie Supreme Pizza; "Fries" = French Fries; "Coke" / "Sprite" / "Soft Drink" (can-sized) = Soft Drink can; "1.5 litre bottle" / "family bottle" = Soft Drink 1.5 litre bottle; "Brownie" = Chocolate Brownie; "Lava Cake" = Choco Lava Cake; "Wings" = Chicken Wings (6 piece). "Thin" / "Regular" alone refer to crust (free) — never a price; put crust in notes, size stays Small/Medium/Large."""

# Server-side price table mirroring MENU above: normalized name → {size → price}.
# Flat-price items use the "" size key. Applied to any zero-price item AFTER
# Gemini, so a missing price is filled even when the LLM leaves 0.
_MENU_PRICES: dict[str, dict[str, float]] = {
    "margherita pizza": {"small": 8.99, "medium": 11.99, "large": 14.99},
    "pepperoni pizza": {"small": 9.99, "medium": 12.99, "large": 15.99},
    "bbq chicken pizza": {"small": 10.99, "medium": 13.99, "large": 16.99},
    "veggie supreme pizza": {"small": 9.99, "medium": 12.99, "large": 15.99},
    "meat lovers pizza": {"small": 11.99, "medium": 14.99, "large": 17.99},
    "four cheese pizza": {"small": 10.99, "medium": 13.99, "large": 16.99},
    "garlic bread": {"": 4.99},
    "garlic bread with cheese": {"": 5.99},
    "chicken wings": {"": 6.99},
    "french fries": {"": 3.49},
    "soft drink can": {"": 1.99},
    "soft drink bottle": {"": 3.49},
    "bottled water": {"": 1.49},
    "chocolate brownie": {"": 4.49},
    "choco lava cake": {"": 4.99},
}

# Customer phrasings → canonical menu names.
_MENU_ALIASES = {
    "margherita": "margherita pizza",
    "pepperoni": "pepperoni pizza",
    "bbq chicken": "bbq chicken pizza",
    "veg supreme": "veggie supreme pizza",
    "veg supreme pizza": "veggie supreme pizza",
    "veggie supreme": "veggie supreme pizza",
    "supreme pizza": "veggie supreme pizza",
    "supreme": "veggie supreme pizza",
    "meat lovers": "meat lovers pizza",
    "meat lover": "meat lovers pizza",
    "four cheese": "four cheese pizza",
    "4 cheese": "four cheese pizza",
    "garlic bread with cheese": "garlic bread with cheese",
    "cheese garlic bread": "garlic bread with cheese",
    "garlic bread": "garlic bread",
    "chicken wings": "chicken wings",
    "wings": "chicken wings",
    "french fries": "french fries",
    "fries": "french fries",
    "soft drink can": "soft drink can",
    "coke": "soft drink can",
    "sprite": "soft drink can",
    "soft drink": "soft drink can",
    "soda": "soft drink can",
    "1.5 litre bottle": "soft drink bottle",
    "1.5 liter bottle": "soft drink bottle",
    "family bottle": "soft drink bottle",
    "soft drink bottle": "soft drink bottle",
    "bottled water": "bottled water",
    "water": "bottled water",
    "chocolate brownie": "chocolate brownie",
    "brownie": "chocolate brownie",
    "choco lava cake": "choco lava cake",
    "lava cake": "choco lava cake",
}

_CRUST_WORDS = {"thin", "regular", "thin crust", "regular crust"}

_MENU_DISPLAY = {
    "margherita pizza": "Margherita Pizza",
    "pepperoni pizza": "Pepperoni Pizza",
    "bbq chicken pizza": "BBQ Chicken Pizza",
    "veggie supreme pizza": "Veggie Supreme Pizza",
    "meat lovers pizza": "Meat Lovers Pizza",
    "four cheese pizza": "Four Cheese Pizza",
    "garlic bread": "Garlic Bread",
    "garlic bread with cheese": "Garlic Bread with Cheese",
    "chicken wings": "Chicken Wings (6 piece)",
    "french fries": "French Fries",
    "soft drink can": "Soft Drink can",
    "soft drink bottle": "Soft Drink 1.5 litre bottle",
    "bottled water": "Bottled Water",
    "chocolate brownie": "Chocolate Brownie",
    "choco lava cake": "Choco Lava Cake",
}


def _lookup_menu_price(name: str, size: str) -> tuple[float, str, str]:
    """(price, canonical_name, size) from MENU; (0, name, size) when no match.

    Crust words (thin/regular) are not sizes — they fall back to Medium
    pricing and the crust is appended to notes by the caller.
    """
    raw = (name or "").strip()
    norm = raw.lower().strip()
    # strip diet tags customers parrot back ("Margherita (Veg)", "Non-Veg")
    norm = re.sub(r"\(.*?\)", "", norm)
    norm = re.sub(r"\bnon\s?-?\s?veg\b", "", norm)
    norm = re.sub(r"\bveg\b", "", norm)
    # strip crust phrases (priced by size, crust is free)
    norm = re.sub(r"\b(thin|regular)\s+crust\b", "", norm)
    norm = re.sub(r"\bcrust\b", "", norm)
    # strip a leading quantity word ("one large veg supreme", "2x coke")
    norm = re.sub(r"^(one|two|three|four|five|six|seven|eight|nine|ten|\d+\s?x?)\b", "", norm)
    norm = re.sub(r"[^a-z0-9 .]", "", norm).strip()
    norm = re.sub(r"\s+", " ", norm)
    size_key = (size or "").strip().lower()
    if not size_key:
        # size baked into the name ("Pepperoni Pizza Large")
        m = re.search(r"\b(small|medium|large)\b", norm)
        if m:
            size_key = m.group(1)
            norm = re.sub(r"\b(small|medium|large)\b", "", norm).strip()
            norm = re.sub(r"\s+", " ", norm)
    key = _MENU_ALIASES.get(norm, norm)
    if key in _MENU_ALIASES:
        key = _MENU_ALIASES[key]
    if "pizza" not in key and f"{key} pizza" in _MENU_PRICES:
        key = f"{key} pizza"
    table = _MENU_PRICES.get(key)
    if table is None:
        # last resort: substring match ("large veg supreme" etc.)
        for cand, t in _MENU_PRICES.items():
            if cand and cand in norm:
                table, key = t, cand
                break
    if table is None:
        return 0.0, raw, size or ""
    if "" in table:
        return table[""], key, ""
    if size_key in _CRUST_WORDS or not size_key:
        return table["medium"], key, size or ""
    return table.get(size_key, table["medium"]), key, size or ""

_PROMPT = """You extract a restaurant order from a phone-call transcript.
First classify the call purpose as one of:
- "order_creation": the customer tried to place a NEW food order (even if incomplete).
- "order_status": the customer asks about an existing order (where is it, is it ready, cancel or change it).
- "reservation": table booking (party size, dates, holding a table).
- "general": anything else (menu questions, hours, complaints, hangup with no order attempt).

Return {"no_order": true, "call_purpose": "<one of the four>", "reason": "<short reason>"} (and nothing else) ONLY when the call is NOT an order_creation attempt:
- order_status / reservation / general calls place nothing new.
- The agent only SUGGESTED items but the customer never engaged with ordering.

When the customer attempted to order (order_creation) ALWAYS return the order JSON below — even when details are missing or it was never confirmed (hangup, transfer, missing name/phone/items). Never use no_order for an order attempt. Put what you heard, empty strings for what you did not, and list the gaps in "missing_fields". Set "order_confirmed": true ONLY when the customer explicitly confirmed it (words like "yes, place it", "confirm", "that's all, go ahead") AND the agent acknowledged it (read back items / total / order number, or said it is confirmed); else false.

Order JSON (ONLY valid JSON, no markdown, no explanation) with exactly these keys:
{
  "call_purpose": "order_creation",
  "order_confirmed": true,
  "order_number": "order ID as spoken in the call (PB- plus digits the agent read out, e.g. PB-105). Copy it EXACTLY as heard, else empty string — do NOT invent one",
  "customer_name": "full name as heard, else empty string — do NOT guess",
  "customer_phone": "digits only, include + and country code if heard, else empty string",
  "order_type": "takeaway or dinein ONLY - delivery, pickup, takeaway and takeout all mean takeaway; use dinein only for eat-in/table service",
  "items": [{"name": "dish as heard (use the exact MENU name when it matches an alias)", "quantity": number, "unit_price": number, "size": "Small/Medium/Large if mentioned else empty", "notes": "crust (Thin/Regular) and extras if mentioned else empty"}],
  "total": total number or 0,
  "delivery_address": "if mentioned else empty string",
  "payment_method": "cash/UPI/card if mentioned else empty string",
  "email": "if mentioned else empty string (optional — never block an order on it)",
  "missing_fields": ["subset of: customer_name, customer_phone, items, order_number, confirmation, payment_method, delivery_address, order_type"]
}
Prices (all in dollars): if the call quotes a price, use it. If the price is missing, look it up in the MENU below and fill it in — a missing price must never block the order. Pizzas with no size heard default to Medium. "Thin"/"Regular" alone mean crust (free) — put crust in notes, never in size, and price by Small/Medium/Large (Medium when unheard). Only items with no menu match stay 0. "total" must equal the sum of quantity × unit_price.
MENU:
""" + MENU + """
Rules: quantities default to 1. Do NOT add any delivery fee yourself — it is computed afterwards.

Transcript:
{transcript}
"""


def _finalize_items(req, delivery_address: str) -> tuple[list[dict], float, bool]:
    """Items + total with server-side delivery fee.

    Subtotal comes from per-item prices (or the LLM total as fallback).
    Any zero-price item is priced from the hardcoded MENU first, so the
    total is correct even when Gemini leaves unit_price at 0. A $2.50
    delivery-fee line is appended when a delivery address exists and the
    subtotal is below $25. Returns (items, total, fee_applied).
    """
    items = []
    for it in req.items:
        price = float(it.unit_price or 0)
        name = (it.name or it.sku or "Item").strip()
        size = (it.size or "").strip()
        notes = (it.notes or "").strip()
        if price <= 0:
            menu_price, canon, menu_size = _lookup_menu_price(name, size)
            if menu_price > 0:
                price = menu_price
                name = _MENU_DISPLAY.get(canon, canon)
                # keep a heard crust word visible in notes
                if size and size.lower() in _CRUST_WORDS and "crust" not in notes.lower():
                    notes = f"{notes}; {size} crust".strip("; ")
                size = menu_size
                logger.info(f"order_recovery: menu-priced '{name}' "
                            f"({size or 'standard'}) at {price}")
        items.append({
            "sku": (it.sku or "")[:128].strip(),
            "name": name[:128],
            "quantity": it.quantity, "unit_price": price,
            "size": size[:32], "notes": notes[:256],
        })
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
    items are replaced only when the shell has none (payload data wins).

    When the transcript attempt is missing fields, the order is flagged
    `incomplete` (instead of looking confirmed) so staff can follow up and
    the dashboard can filter it out of the fulfilment queue.
    """
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
    missing = _compute_missing(req, "")
    # keep caller-provided values in the event note; recompute without text
    # is conservative (order_number may already be on the shell).
    if order.order_number:
        missing = [m for m in missing if m != "order_number"]
    if missing:
        order.status = INCOMPLETE_STATUS
        note = f"Order extracted from transcript via Google LLM (incomplete: {', '.join(missing)})"
    else:
        if order.status == INCOMPLETE_STATUS:
            order.status = "confirmed"
        note = "Order extracted from transcript via Google LLM"
    order.source = "transcript_recovery"
    order.events.append(OrderEvent(order_id=order.id, status=order.status,
                                   note=note))


async def _get_google(db) -> GoogleSetting:
    row = (await db.execute(select(GoogleSetting).where(GoogleSetting.id == 1))).scalar_one_or_none()
    if not row:
        row = GoogleSetting(id=1, api_key="", model="gemini-3.5-flash")
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
    from app.services.gemini import generate_content
    for attempt in ("extract", "repair"):
        prompt = _PROMPT.replace("{transcript}", transcript[:MAX_TRANSCRIPT_CHARS])
        if attempt == "repair":
            prompt += "\nYour previous reply was not valid JSON. Reply with JSON only."
        try:
            text = await generate_content(
                api_key, model or _FALLBACK_MODEL,
                [{"parts": [{"text": prompt}]}],
                {"responseMimeType": "application/json"},
                timeout=60,
            )
        except Exception as e:
            logger.warning(
                f"order_recovery: Gemini call failed ({attempt}): {_redact_key(str(e))} "
                "— check the API key and model name in Integrations → Google settings."
            )
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


async def analyze_transcript(text: str, api_key: str, model: str) -> dict:
    """Transcript → {req, purpose, missing, no_order, reason}.

    - purpose is ALWAYS classified (order_creation | order_status |
      reservation | general) so the Call can be tagged for filtering even
      when no order row is created.
    - order_creation attempts ALWAYS yield a req (even partial) — the caller
      saves it with status `incomplete` when fields are missing.
    - order_status / reservation / general yield req=None (no order row).
    """
    from app.schemas import OrderCreateRequest as _OCR
    data = await _ask_gemini(text, api_key, model or "gemini-3.5-flash")
    if not data:
        # LLM call failed (quota/5xx/invalid JSON) — unknown stays unknown:
        # callers must NOT tag the call "general" off a failed lookup.
        return {"req": None, "purpose": "", "missing": [],
                "no_order": True, "reason": "llm_error"}
    purpose = _normalize_purpose(data.get("call_purpose"), data, text)
    if data.get("no_order"):
        if data.get("reason"):
            logger.info(f"order_recovery: no order ({data.get('reason')}) purpose={purpose}")
        if purpose not in CALL_PURPOSES:
            purpose = "general"
        return {"req": None, "purpose": purpose, "missing": [],
                "no_order": True, "reason": str(data.get("reason") or "")}
    try:
        req = _OCR.model_validate(data)
    except Exception as e:
        logger.warning(f"order_recovery: Gemini JSON failed validation: {e}")
        return {"req": None, "purpose": purpose, "missing": [],
                "no_order": True, "reason": "validation"}
    # Order JSON without an explicit purpose is an order_creation attempt.
    purpose = _normalize_purpose(req.call_purpose or data.get("call_purpose"),
                                 data, text, order_json=True)
    req.call_purpose = purpose
    if purpose in _NON_ORDER_PURPOSES:
        logger.info(f"order_recovery: {purpose} call — tagging only, no order row")
        return {"req": None, "purpose": purpose, "missing": [],
                "no_order": True, "reason": purpose}
    missing = _compute_missing(req, text)
    req.missing_fields = missing
    return {"req": req, "purpose": "order_creation", "missing": missing,
            "no_order": False, "reason": ""}


async def extract_request_from_text(text: str, api_key: str, model: str):
    """Back-compat wrapper: returns the req only for order_creation attempts.

    Status/reservation/general calls → None (no order row; use
    analyze_transcript when you also need the purpose tag). Incomplete
    order_creation attempts still return a req — the caller marks it
    `incomplete` instead of dropping it.
    """
    result = await analyze_transcript(text, api_key, model)
    return result["req"]


async def _next_sequential_number(db, start: int | None = None) -> str:
    """First free PB-{n} starting at total orders + 1 (bumping past gaps).

    Shared by recovery builds and the next-number endpoint so the spoken
    number (fetched at call start) and the stored number agree.
    """
    from sqlalchemy import func as _func
    total = (await db.execute(select(_func.count()).select_from(Order))).scalar_one() or 0
    candidate = start or ((total or 0) + 1)
    for _ in range(1000):
        num = f"PB-{candidate}"
        exists = (await db.execute(
            select(Order.id).where(Order.order_number == num))).scalar_one_or_none()
        if exists is None:
            return num
        candidate += 1
    # Practically unreachable — fall back to a random code rather than failing.
    return f"PB-{''.join(secrets.choice(_ORDER_CODE_ALPHABET) for _ in range(6))}"


async def _unique_generated_number(db) -> str:
    return await _next_sequential_number(db)


async def build_order_from_req(db, req, missing: list[str], text: str,
                               call: Call, customer_name: str,
                               customer_phone: str, call_id: str):
    """Persist an order_creation req (complete → confirmed, else incomplete).

    Shared by the webhook straight path and the background recovery job so
    both behave identically. Returns the Order (reusing on duplicate number).
    """
    from app.schemas import clean_str as _clean
    from sqlalchemy.exc import IntegrityError as _IntegrityError
    items, total, _fee = _finalize_items(req, _clean(req.delivery_address))
    # Never store an agent-hallucinated number ("P-B two four"): only a
    # valid PB-style code is kept, otherwise fall back to transcript text,
    # otherwise a plausible legacy code ONLY if it matches a real order row,
    # otherwise the next sequential PB-{n}.
    raw_num = (_clean(req.order_number) or "").strip().upper()
    number = _valid_order_number(raw_num) or _extract_order_number_from_text(text or "")
    if not number:
        # Pre-reserved PB-{total+1} from call start (Dograh fetched
        # GET /api/orders/next-number and stored it in initial_context so the
        # agent speaks the same code). Keeps transcript == DB when Gemini
        # missed the spoken number.
        try:
            scopes = [getattr(call, "initial_context", None) or {},
                      getattr(call, "gathered_context", None) or {}]
            for scope in scopes:
                if not isinstance(scope, dict):
                    continue
                for key in ("reserved_order_number", "next_order_number",
                            "order_number", "pb_number"):
                    cand = _valid_order_number(scope.get(key))
                    if cand:
                        number = cand
                        break
                if number:
                    break
        except Exception:
            pass
        if number:
            from sqlalchemy import func as _func
            taken = (await db.execute(
                select(Order.id).where(_func.upper(Order.order_number) == number)
            )).scalar_one_or_none()
            if taken is not None:
                number = ""  # taken by another call → fresh sequential below
    if not number and _plausible_order_number(raw_num):
        from sqlalchemy import func as _func
        dup = (await db.execute(
            select(Order).where(_func.upper(Order.order_number) == raw_num)
        )).scalar_one_or_none()
        # Reuse only same-call / unlinked shells — a number owned by ANOTHER
        # call means two calls raced on one fetched code; fall through to a
        # fresh sequential number instead of writing into the wrong order.
        if dup is not None and (dup.call_id or "") in ("", call_id):
            return dup
    missing = list(missing or [])
    phone = (req.customer_phone or "").strip() or customer_phone or _extract_phone_from_text(text or "")
    name = (req.customer_name or "").strip() or customer_name
    if "customer_phone" in missing and phone:
        missing = [m for m in missing if m != "customer_phone"]
    if "customer_name" in missing and name:
        missing = [m for m in missing if m != "customer_name"]
    if number and "order_number" in missing:
        # Resolved from transcript/contexts after the LLM left it empty
        # (e.g. pre-reserved PB-{n} from initial_context) — not actually
        # missing, so it must not force `incomplete`.
        missing = [m for m in missing if m != "order_number"]
    if not number:
        number = await _next_sequential_number(db)
        if "order_number" not in missing:
            missing.append("order_number")
    status = INCOMPLETE_STATUS if missing else "confirmed"
    order = Order(
        order_number=number, customer_name=name, customer_phone=phone,
        email=_clean(req.email), order_type=req.order_type,
        table_number=_clean(req.table_number), pickup_time=_clean(req.pickup_time),
        delivery_address=_clean(req.delivery_address),
        payment_method=_clean(req.payment_method),
        status=status, total=Decimal(str(total)),
        source="transcript_recovery", call_id=call_id,
    )
    db.add(order)
    try:
        # Savepoint: a concurrent call racing on the same sequential code
        # rolls back only this insert, then we retry with the next number —
        # the outer transaction (call row, memory) stays intact.
        async with db.begin_nested():
            await db.flush()
    except _IntegrityError:
        number = await _next_sequential_number(db)
        order.order_number = number
        async with db.begin_nested():
            await db.flush()
        logger.info(f"order_recovery: sequential collision, stored as {number} for call {call_id!r}")
    for it in items:
        db.add(OrderItem(
            order_id=order.id, menu_item_id=None,
            sku=it["sku"], name=it["name"], quantity=it["quantity"],
            unit_price=Decimal(str(it["unit_price"])),
            size=it["size"], notes=it["notes"]))
    note = ("Order extracted from transcript via Google LLM"
            if status == "confirmed"
            else f"Order extracted from transcript via Google LLM (incomplete: {', '.join(missing)})")
    db.add(OrderEvent(order_id=order.id, status=status, note=note))
    return order


async def get_google_key(db) -> tuple[str, str, bool]:
    """(api_key, model, enabled) — dashboard key first, env fallback."""
    g = await _get_google(db)
    api_key = (g.api_key or "").strip() or settings.gemini_api_key
    return api_key, (g.model or "gemini-3.5-flash"), bool(g.recovery_enabled)


async def create_order_from_transcript(
    db, text: str, call: Call, customer_name: str, customer_phone: str, call_id: str,
    note: str = "Order extracted from transcript via Google LLM",
):
    """Build an order row straight from transcript text.

    - order_status / reservation / general → None (call is tagged, no row).
    - order_creation with missing fields → row with status `incomplete`
      (order_number generated when none was heard) so staff can follow up
      and the dashboard can filter it separately.
    - complete order_creation → status `confirmed` as before.
    """
    from app.schemas import clean_str as _clean
    api_key, model, enabled = await get_google_key(db)
    if not enabled or not api_key or not (text or "").strip():
        return None
    result = await analyze_transcript(text, api_key, model)
    purpose = result["purpose"]
    try:
        if (call.call_purpose or "").strip() in ("", "general") and purpose:
            call.call_purpose = purpose
    except Exception:
        pass
    req = result["req"]
    if req is None:
        return None
    if result["purpose"] == "order_creation" and (result["missing"] or []):
        logger.info(
            "order_recovery: incomplete order "
            f"({', '.join(result['missing'])}), saving as incomplete")

    order = await build_order_from_req(
        db, req, list(result["missing"] or []), text,
        call, customer_name, customer_phone, call_id)
    if order.order_number not in (call.order_numbers or []):
        call.order_numbers = [*(call.order_numbers or []), order.order_number]
    return order


async def _transcript_from_run(db, call: Call) -> str:
    """Cut-call fallback: the post-call webhook often arrives with no
    transcript when the caller hangs up. Pull it from the voice platform run
    instead (matched by call UUID in gathered context), persist it on the
    call, and return the text for extraction. Needs the Dograh API key."""
    try:
        from app.services import dograh_client as _dc
        row = (await db.execute(
            select(DograhSetting).where(DograhSetting.id == 1))).scalar_one_or_none()
        api_key = (row.api_key if row else "" or "").strip()
        base_url = (row.base_url if row else "") or ""
        if not api_key or not (call.call_id or "").strip():
            return ""
        run_id, wf_id = call.run_id, call.workflow_id
        if not run_id or not wf_id:
            # Scan recent runs for the one whose context carries our call UUID.
            page, scanned = 1, 0
            while scanned < 500:
                data = await _dc.list_runs(api_key, base_url, page=page, limit=100)
                runs = data.get("runs", []) or []
                if not runs:
                    break
                for r in runs:
                    gc = r.get("gathered_context") or {}
                    if gc.get("call_id") == call.call_id or str(r.get("id")) == call.call_id:
                        run_id, wf_id = r.get("id"), r.get("workflow_id")
                        break
                if run_id and wf_id:
                    call.run_id, call.workflow_id = run_id, wf_id
                    break
                scanned += len(runs)
                if page >= data.get("total_pages", 1):
                    break
                page += 1
        if not run_id or not wf_id:
            return ""
        detail = await _dc.get_run_detail(api_key, base_url, wf_id, run_id)
        turl = detail.get("transcript_public_url") or detail.get("transcript_url") or ""
        if not turl:
            return ""
        text = await _dc.fetch_public_text(turl, base_url)
        if text.strip():
            if not call.transcript:
                call.transcript = [{"role": "transcript", "content": text}]
            if not call.transcript_url:
                call.transcript_url = turl
            logger.info(f"order_recovery: transcript pulled from run {run_id} for call {call.call_id!r}")
        return text
    except Exception as e:
        logger.warning(f"order_recovery: run transcript fallback failed: {e}")
        return ""


async def recover_order_from_call(call_id: str, order_number: str | None = None) -> dict:
    """Background entry point: extract one call's order from its transcript.

    - order_status / reservation / general → no order row; the Call is still
      tagged with its purpose (committed) so it can be filtered.
    - order_creation (complete or partial) → order row created/updated;
      partial attempts get status `incomplete`.
    """
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
            # Cut call: webhook carried no transcript → try the voice run.
            text = await _transcript_from_run(db, call) or ""
        if not text.strip():
            return {"status": "skipped", "reason": "no transcript"}
        if not call.transcript:
            # Persist the downloaded text so the Call Log dialog can show
            # the conversation even when the webhook carried no turns.
            call.transcript = [{"role": "transcript", "content": text[:MAX_TRANSCRIPT_CHARS]}]

        result = await analyze_transcript(text, api_key, g.model or "gemini-3.5-flash")
        purpose = result["purpose"]
        if (call.call_purpose or "").strip() in ("", "general") and purpose:
            call.call_purpose = purpose
        req = result["req"]
        if req is None:
            await db.commit()  # persist the purpose tag even with no order
            reason = result.get("reason") or "no order in transcript"
            return {"status": "skipped", "reason": reason,
                    "call_purpose": purpose, "call_id": call_id}

        if order:
            _apply_extraction(order, req, call, shell_empty)
            if not (order.customer_phone or "").strip():
                order.customer_phone = _extract_phone_from_text(text)
        else:
            order = await build_order_from_req(
                db, req, list(result["missing"] or []), text,
                call, call.customer_name, call.customer_phone, call_id)
            if order is None:  # pragma: no cover — defensive
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
                "order_status": order.status,
                "call_purpose": call.call_purpose or purpose,
                "call_id": call_id}
