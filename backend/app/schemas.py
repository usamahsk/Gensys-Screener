from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

JUNK_STRINGS = {"none", "n/a", "na", "null", "nil", "-", "no", "not provided"}


def is_unresolved_template(v: Any) -> bool:
    """Dograh leaves {{...}} literals when a variable is missing."""
    return isinstance(v, str) and "{{" in v and "}}" in v


def clean_str(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if not s:
        return ""
    if is_unresolved_template(s):
        return ""
    # literal placeholder the user pasted, e.g. "<payment_method>"
    if s.startswith("<") and s.endswith(">"):
        return ""
    return "" if s.lower() in JUNK_STRINGS else s


def coerce_bool(v: Any) -> bool:
    """Tolerate Dograh string bools: '', 'true'/'false', '1'/'0', 1/0, {{...}}."""
    if isinstance(v, bool):
        return v
    if v in (None, ""):
        return False
    if is_unresolved_template(v):
        return False
    s = str(v).strip().lower()
    if s in ("", "none", "null", "nil", "n/a", "na", "no", "not provided", "-"):
        return False
    if s in ("true", "1", "yes", "y", "confirmed", "completed", "success", "successful"):
        return True
    if s in ("false", "0", "no", "n", "failed", "failure", "cancelled", "canceled"):
        return False
    # Pydantic default would 422 on arbitrary strings — fall back to False
    # for unknown, but True for any other truthy string? Keep strict-ish:
    return bool(s) and s not in ("0", "false")


# ---------- Agent tool payloads ----------

class OrderItemIn(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    sku: str = ""
    name: str = ""
    quantity: int = Field(default=1, ge=1, le=20)
    unit_price: float = 0
    size: Optional[str] = ""
    notes: Optional[str] = ""

    @model_validator(mode="before")
    @classmethod
    def _item_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # tolerate "payment_method "-style keys with stray spaces
            stripped = {str(k).strip(): v for k, v in data.items()}
            if stripped is not data and any(str(k) != str(k).strip() for k in data):
                data = stripped
            # case-insensitive keys ("Name", "Quantity", "UnitPrice"...)
            lowered = {str(k).strip().lower(): v for k, v in data.items()}
            for canonical in ("sku", "name", "quantity", "unit_price", "price",
                              "size", "notes", "item", "dish", "product", "title"):
                if canonical not in data and canonical not in lowered:
                    continue
                if canonical in lowered and canonical not in data:
                    data[canonical] = lowered[canonical]
            # price == unit_price; item/item_name/dish == name
            if "price" in data and "unit_price" not in data:
                data["unit_price"] = data["price"]
            if "unitprice" in lowered and "unit_price" not in data and "price" not in data:
                data["unit_price"] = lowered["unitprice"]
            for alt in ("item", "item_name", "itemname", "dish", "dish_name", "dishname",
                        "product", "product_name", "productname", "title",
                        "menu_item", "menu item", "menuitem"):
                if alt in lowered and "name" not in data:
                    data["name"] = lowered[alt]
                    break
        return data

    @field_validator("sku", "name", "size", "notes", mode="before")
    @classmethod
    def _coerce_str(cls, v: Any) -> Any:
        if v is None:
            return ""
        if is_unresolved_template(v):
            return ""
        return v

    @field_validator("sku", "name", "size", "notes", mode="after")
    @classmethod
    def _trunc_to_columns(cls, v: Any, info: Any) -> Any:
        """Never 500 on varchar limits — LLM puts dish names in `sku` (col=32)."""
        limits = {"sku": 128, "name": 128, "size": 32, "notes": 256}
        return str(v or "")[: limits.get(info.field_name, 128)]

    @field_validator("quantity", mode="before")
    @classmethod
    def _coerce_qty(cls, v: Any) -> Any:
        if v in (None, "") or is_unresolved_template(v):
            return 1
        try:
            return int(float(str(v)))
        except (ValueError, TypeError):
            return 1

    @field_validator("unit_price", mode="before")
    @classmethod
    def _coerce_price(cls, v: Any) -> Any:
        if v in (None, "") or is_unresolved_template(v):
            return 0
        if isinstance(v, str):
            # tolerate "$15.99", "15.99 USD", "1,299.00"
            cleaned = re.sub(r"[^\d.\-]", "", v.replace(",", ""))
            if cleaned and cleaned not in ("-", "."):
                try:
                    return float(cleaned)
                except (ValueError, TypeError):
                    pass
            return 0
        try:
            return float(v)  # type: ignore[arg-type]
        except (ValueError, TypeError):
            return 0


class OrderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    order_number: str = ""
    customer_name: str = ""
    customer_phone: str = Field(default="", min_length=0, max_length=20)
    email: Optional[str] = ""
    order_type: str = "takeaway"
    table_number: Optional[str] = ""
    pickup_time: Optional[str] = ""
    delivery_address: Optional[str] = ""
    payment_method: Optional[str] = ""
    items: list[OrderItemIn] = Field(default_factory=list)
    total: Optional[float] = None
    call_id: Optional[str] = ""
    # --- Transcript-extraction flags (Gemini only; ignored on tool calls) ---
    # order_confirmed: customer explicitly confirmed placement AND the agent
    # acknowledged it. no_order: status check / reservation / incomplete call.
    order_confirmed: bool = False
    no_order: bool = False
    # Call-purpose classification from the transcript classifier:
    # order_creation | order_status | reservation | general. Also accepted on
    # tool calls (ignored) so one schema fits both paths.
    call_purpose: str = ""
    missing_fields: list[str] = []
    # --- Voice-platform provider + telephony id (Dograh flavour of Genesys id) ---
    provider: Optional[str] = ""
    provider_call_id: Optional[str] = ""
    telephony_id: Optional[str] = ""
    # --- Dograh webhook context (all optional, also accepted on order-status) ---
    workflow_run_id: Optional[str] = ""
    first_name: Optional[str] = ""
    rsvp: Optional[str] = ""
    duration: Optional[Any] = 0
    duration_seconds: Optional[Any] = 0
    recording_url: Optional[str] = ""
    user_recording_url: Optional[str] = ""
    bot_recording_url: Optional[str] = ""
    transcript_url: Optional[str] = ""
    # Full provider contexts (Dograh run initial/gathered) + captured intent.
    # Accepted as dicts or JSON strings; stored on the Call Logs row.
    initial_context: dict[str, Any] = {}
    gathered_context: dict[str, Any] = {}
    customer_intent: Optional[str] = ""

    @model_validator(mode="before")
    @classmethod
    def _map_aliases(cls, data: Any) -> Any:
        """Tolerate LLM-invented key names (spaces, synonyms)."""
        if not isinstance(data, dict):
            return data
        # 1) strip stray whitespace in keys ("payment_method " etc.)
        stripped: dict[str, Any] = {}
        for k, v in data.items():
            sk = str(k).strip()
            if sk not in stripped:
                stripped[sk] = v
        data = stripped
        aliases = {
            "order number": "order_number",
            "order id": "order_number",
            "order_id": "order_number",
            "ordernumber": "order_number",
            "customer phone number": "customer_phone",
            "phone number": "customer_phone",
            "phone": "customer_phone",
            "customer name": "customer_name",
            "name": "customer_name",
            "order items": "items",
            "order_items": "items",
            "orderitems": "items",
            "dishes": "items",
            "products": "items",
            "cart": "items",
            "line_items": "items",
            "line items": "items",
            "lineitems": "items",
            "order_details": "items",
            "order details": "items",
            "orderdetails": "items",
            "order type": "order_type",
            "fulfillment type": "order_type",
            "fulfillment_type": "order_type",
            "table": "table_number",
            "pickup time": "pickup_time",
            "delivery address": "delivery_address",
            "payment method": "payment_method",
            "payment_method": "payment_method",
            "workflow run id": "call_id",
            "workflow_run_id": "call_id",
            "telephony id": "provider_call_id",
            "telephony_id": "provider_call_id",
            "telephony call id": "provider_call_id",
            "provider call id": "provider_call_id",
            "provider_call_id": "provider_call_id",
            "provider id": "provider_call_id",
            "call sid": "provider_call_id",
            "twilio sid": "provider_call_id",
            "first name": "first_name",
            "user recording url": "user_recording_url",
            "bot recording url": "bot_recording_url",
            "user_recording_url": "user_recording_url",
            "bot_recording_url": "bot_recording_url",
            "transcript url": "transcript_url",
            "transcript_url": "transcript_url",
        }
        normalized = {str(k).strip().lower().replace("-", " "): v for k, v in data.items()}
        for alias, canonical in aliases.items():
            if alias in normalized and canonical not in data and canonical not in normalized:
                data[canonical] = normalized[alias]
        return data

    @field_validator("items", mode="before")
    @classmethod
    def _coerce_items(cls, v: Any) -> Any:
        """Accept items as a JSON string (some LLMs stringify arrays)."""
        if v in (None, "") or is_unresolved_template(v):
            return []
        if isinstance(v, dict):
            return [v]
        if isinstance(v, str):
            import ast
            import json

            s = v.strip()
            if not s or is_unresolved_template(s):
                return []
            parsed: Any = None
            try:
                parsed = json.loads(s)
            except (json.JSONDecodeError, ValueError):
                try:
                    parsed = ast.literal_eval(s)
                except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                    return []
            if isinstance(parsed, dict):
                return [parsed]
            if isinstance(parsed, list):
                return parsed
            return []
        return v

    @field_validator("duration", "duration_seconds", mode="before")
    @classmethod
    def _coerce_duration(cls, v: Any) -> Any:
        """Dograh templates may resolve to '' or numeric strings."""
        if v in (None, "") or is_unresolved_template(v):
            return 0
        try:
            return int(float(str(v)))
        except (ValueError, TypeError):
            return 0

    @field_validator("total", mode="before")
    @classmethod
    def _coerce_total(cls, v: Any) -> Any:
        if v in (None, "") or is_unresolved_template(v):
            return None
        try:
            return float(str(v))
        except (ValueError, TypeError):
            return None

    @field_validator("order_confirmed", "no_order", mode="before")
    @classmethod
    def _coerce_flags(cls, v: Any) -> Any:
        return coerce_bool(v) if not isinstance(v, bool) else v

    @field_validator("call_purpose", mode="before")
    @classmethod
    def _coerce_purpose(cls, v: Any) -> Any:
        if v in (None, "") or is_unresolved_template(v):
            return ""
        s = str(v).strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "create_order": "order_creation", "order_creation": "order_creation",
            "new_order": "order_creation", "place_order": "order_creation",
            "order": "order_creation",
            "order_status": "order_status", "order_status_check": "order_status",
            "status_check": "order_status", "track_order": "order_status",
            "reservation": "reservation", "table_booking": "reservation",
            "book_table": "reservation", "booking": "reservation",
        }
        if s in aliases:
            return aliases[s]
        return s if s in ("order_creation", "order_status", "reservation", "general") else ""

    @field_validator("missing_fields", mode="before")
    @classmethod
    def _coerce_missing(cls, v: Any) -> Any:
        if v in (None, "") or is_unresolved_template(v):
            return []
        if isinstance(v, str):
            return [v.strip()] if v.strip() else []
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return []

    @field_validator("customer_name", "customer_phone", "email", "table_number",
                     "pickup_time", "delivery_address", "payment_method",
                     "call_id", "workflow_run_id", "first_name", "rsvp",
                     "recording_url", "user_recording_url", "bot_recording_url",
                     "transcript_url", "order_number", mode="before")
    @classmethod
    def _coerce_str_fields(cls, v: Any) -> Any:
        if v is None:
            return ""
        if is_unresolved_template(v):
            return ""
        s = str(v).strip()
        if s.startswith("<") and s.endswith(">"):
            return ""
        return s

    @field_validator("provider", "provider_call_id", "telephony_id", mode="before")
    @classmethod
    def _coerce_provider_fields(cls, v: Any) -> Any:
        if v is None:
            return ""
        if is_unresolved_template(v):
            return ""
        s = str(v).strip()
        if s.startswith("<") and s.endswith(">"):
            return ""
        return s[:128]

    @field_validator("order_type", mode="before")
    @classmethod
    def _coerce_order_type(cls, v: Any) -> Any:
        """Map delivery/pickup/dine-in variants → takeaway|dinein (never 422)."""
        if v in (None, "") or is_unresolved_template(v):
            return "takeaway"
        s = str(v).strip().lower().replace("-", "").replace("_", "").replace(" ", "")
        if s in ("dinein", "dine", "indining", "table"):
            return "dinein"
        # delivery, pickup, takeaway, takeout, collection → takeaway
        return "takeaway"

    @field_validator("initial_context", "gathered_context", mode="before")
    @classmethod
    def _coerce_contexts(cls, v: Any) -> Any:
        if isinstance(v, dict):
            return v
        if v in (None, "") or is_unresolved_template(v):
            return {}
        if isinstance(v, str):
            import json
            try:
                parsed = json.loads(v.strip())
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, ValueError, AttributeError):
                return {}
        return {}

    @field_validator("customer_intent", mode="before")
    @classmethod
    def _coerce_intent(cls, v: Any) -> Any:
        if v in (None, "") or is_unresolved_template(v):
            return ""
        s = str(v).strip()
        if s.startswith("<") and s.endswith(">"):
            return ""
        return s[:64]

    def resolved_call_id(self) -> str:
        return (self.call_id or self.workflow_run_id or "").strip()

    def resolved_provider(self) -> str:
        p = clean_str(self.provider).lower() or "dograh"
        return p[:32]

    def resolved_provider_call_id(self) -> str:
        return clean_str(self.provider_call_id or self.telephony_id)[:128]

    def resolved_duration(self) -> int:
        try:
            return int(self.duration_seconds or self.duration or 0)
        except (ValueError, TypeError):
            return 0

    def resolved_first_name(self) -> str:
        return clean_str(self.first_name)

    def resolved_customer_intent(self) -> str:
        return clean_str(self.customer_intent)

    def resolved_initial_context(self) -> dict[str, Any]:
        return self.initial_context if isinstance(self.initial_context, dict) else {}

    def resolved_gathered_context(self) -> dict[str, Any]:
        return self.gathered_context if isinstance(self.gathered_context, dict) else {}


class OrderItemOut(BaseModel):
    sku: str
    name: str
    quantity: int
    unit_price: Decimal
    size: str
    notes: str

    class Config:
        from_attributes = True


class OrderEventOut(BaseModel):
    status: str
    note: str
    created_at: datetime

    class Config:
        from_attributes = True


class OrderOut(BaseModel):
    order_number: str
    customer_name: str
    customer_phone: str
    email: str
    order_type: str
    table_number: str
    pickup_time: str
    delivery_address: str = ""
    payment_method: str = ""
    status: str
    total: Decimal
    source: str
    call_id: str
    provider: str = ""
    provider_call_id: str = ""
    created_at: datetime
    items: list[OrderItemOut] = []
    events: list[OrderEventOut] = []

    class Config:
        from_attributes = True


class OrderCreatedResponse(BaseModel):
    order_number: str
    total: Decimal
    status: str
    items: list[OrderItemOut]
    # Agent instruction channel: pending responses tell the LLM not to invent
    # an order number; confirmed responses leave it empty.
    message: str = ""


class OrderStatusResponse(BaseModel):
    found: bool
    order: Optional[OrderOut] = None
    message: Optional[str] = None


class MemoryRecordRequest(BaseModel):
    text: str
    kind: str = Field(default="note", pattern="^(order|trace|note|call_summary)$")


class MemoryOut(BaseModel):
    kind: str
    text: str
    created_at: datetime

    class Config:
        from_attributes = True


class MemoryResponse(BaseModel):
    phone: str
    name: str
    last_intent: str
    last_order_number: str
    context: dict[str, Any]
    recent_memories: list[MemoryOut]


# ---------- Webhook payloads (Dograh -> app) ----------

class CallEndedWebhook(BaseModel):
    """Post-call summary payload.

    Accepts both our original flat shape AND the Dograh post-call template
    shape (gathered_context / cost_info / recording urls). Every field has a
    default so a template that resolves to an empty string is tolerated.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    # identity — friend sends call_id={{workflow_run_id}}
    call_id: str = ""
    workflow_run_id: str = ""
    # voice-platform provider + provider-side telephony id
    # (Dograh flavour of the Genesys conversation id).
    provider: str = ""
    provider_call_id: str = ""
    telephony_id: str = ""
    telephony_call_id: str = ""
    call_sid: str = ""

    # customer
    first_name: str = ""
    rsvp: str = ""
    customer_name: str = ""
    customer_phone: str = ""
    phone: str = ""
    email: str = ""
    # Nested Dograh contexts — the ANI phone (caller_number / from_number)
    # and often the name live here when the post-call template doesn't map
    # them to the top-level customer_* fields. Accept dicts or JSON strings.
    gathered_context: dict[str, Any] = {}
    initial_context: dict[str, Any] = {}
    customer: dict[str, Any] = {}

    # cost / recording
    duration: int = 0
    duration_seconds: int = 0
    cost_info: dict[str, Any] = {}
    cost: float | None = None
    recording_url: str = ""
    user_recording_url: str = ""
    bot_recording_url: str = ""
    transcript_url: str = ""
    transcript: list[dict] = []
    tool_calls: list[dict] = []

    # outcome
    call_outcome: str = ""
    call_completed_successfully: bool = True
    sentiment: str = ""
    disposition: str = "completed"

    # order (materialised into the orders table when present)
    order_completed: bool = False
    order_confirmed: bool = False
    order_number: str = ""
    order_items: Any = None  # JSON array or text
    total_price: float = 0
    fulfillment_type: str = ""
    delivery_address: str = ""
    payment_method: str = ""
    order_numbers: list[str] = []

    # reservation (not used in Pizza Bay scope — tolerated)
    reservation_completed: bool = False
    reservation_confirmed: bool = False
    party_size: int | None = None
    reservation_date: str = ""
    reservation_time: str = ""

    # escalation
    transfer_requested: bool = False
    transfer_reason: str = ""
    genesys_conversation_id: str = ""

    @model_validator(mode="before")
    @classmethod
    def _map_aliases(cls, data: Any) -> Any:
        """Accept order-tool names + stray-space keys from Dograh templates.

        Your webhook body uses order_type/items/total (the create_order names);
        this endpoint natively uses fulfillment_type/order_items/total_price.
        Map them so the order still materialises instead of being dropped.
        Also strips trailing spaces, e.g. 'payment_method ' → 'payment_method'.
        """
        if not isinstance(data, dict):
            return data
        # 1) strip stray whitespace in keys ("payment_method " etc.)
        stripped: dict[str, Any] = {}
        for k, v in data.items():
            sk = str(k).strip()
            if sk not in stripped:
                stripped[sk] = v
        data = stripped
        aliases = {
            "order_type": "fulfillment_type",
            "fulfillment type": "fulfillment_type",
            "items": "order_items",
            "order items": "order_items",
            "orderitems": "order_items",
            "total": "total_price",
            "total_price": "total_price",
            "totalprice": "total_price",
            "phone": "customer_phone",
            "phone number": "customer_phone",
            "customer phone number": "customer_phone",
            "customer name": "customer_name",
            "name": "customer_name",
            "first name": "first_name",
            "workflow run id": "call_id",
            "workflow_run_id": "call_id",
            "telephony id": "provider_call_id",
            "telephony_id": "provider_call_id",
            "telephony call id": "telephony_call_id",
            "provider call id": "provider_call_id",
            "provider_call_id": "provider_call_id",
            "provider id": "provider_call_id",
            "call sid": "call_sid",
            "call_sid": "call_sid",
            "twilio sid": "call_sid",
            "payment method": "payment_method",
            "delivery address": "delivery_address",
            "user recording url": "user_recording_url",
            "bot recording url": "bot_recording_url",
            "transcript url": "transcript_url",
            "duration_seconds": "duration",
            "call_duration_seconds": "duration",
        }
        normalized = {str(k).strip().lower().replace("-", " "): v for k, v in data.items()}
        for alias, canonical in aliases.items():
            if alias in normalized and not data.get(canonical):
                data[canonical] = normalized[alias]
        return data

    @field_validator("order_completed", "order_confirmed",
                     "reservation_completed", "reservation_confirmed",
                     "transfer_requested", mode="before")
    @classmethod
    def _coerce_bools(cls, v: Any) -> Any:
        return coerce_bool(v) if not isinstance(v, bool) else v

    @field_validator("call_completed_successfully", mode="before")
    @classmethod
    def _coerce_call_ok(cls, v: Any) -> Any:
        # default True when missing/empty (a normal completed call)
        if v in (None, "") or is_unresolved_template(v):
            return True
        return coerce_bool(v)

    @field_validator("duration", "duration_seconds", mode="before")
    @classmethod
    def _coerce_ints(cls, v: Any) -> Any:
        """Dograh templates may resolve to '' or stay unresolved ('{{...}}')."""
        if v in (None, "") or is_unresolved_template(v):
            return 0
        try:
            return int(float(str(v)))
        except (ValueError, TypeError):
            return 0

    @field_validator("cost", mode="before")
    @classmethod
    def _coerce_cost(cls, v: Any) -> Any:
        if v in (None, "") or is_unresolved_template(v):
            return None
        try:
            return float(str(v))
        except (ValueError, TypeError):
            return None

    @field_validator("total_price", mode="before")
    @classmethod
    def _coerce_total(cls, v: Any) -> Any:
        if v in (None, "") or is_unresolved_template(v):
            return 0
        try:
            return float(str(v))
        except (ValueError, TypeError):
            return 0

    @field_validator("call_id", "workflow_run_id", "first_name", "rsvp",
                     "customer_name", "customer_phone", "phone", "email",
                     "recording_url", "user_recording_url", "bot_recording_url",
                     "transcript_url", "call_outcome", "sentiment", "disposition",
                     "order_number", "fulfillment_type", "delivery_address",
                     "payment_method", "transfer_reason", "genesys_conversation_id",
                     "provider", "provider_call_id", "telephony_id",
                     "telephony_call_id", "call_sid",
                     "reservation_date", "reservation_time", mode="before")
    @classmethod
    def _coerce_strs(cls, v: Any) -> Any:
        if v is None:
            return ""
        if is_unresolved_template(v):
            return ""
        s = str(v).strip()
        if s.startswith("<") and s.endswith(">"):
            return ""
        return s

    @field_validator("cost_info", "gathered_context", "initial_context",
                      "customer", mode="before")
    @classmethod
    def _coerce_cost_info(cls, v: Any) -> Any:
        if isinstance(v, dict):
            return v
        if v in (None, "") or is_unresolved_template(v):
            return {}
        if isinstance(v, str):
            import json
            try:
                parsed = json.loads(v.strip())
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, ValueError, AttributeError):
                return {}
        return {}

    @field_validator("transcript", "tool_calls", mode="before")
    @classmethod
    def _coerce_list_dicts(cls, v: Any) -> Any:
        if v in (None, "") or is_unresolved_template(v):
            return []
        if isinstance(v, str):
            import json
            s = v.strip()
            if not s or is_unresolved_template(s):
                return []
            try:
                parsed = json.loads(s)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, ValueError):
                return []
        return v

    @field_validator("order_numbers", mode="before")
    @classmethod
    def _coerce_order_numbers(cls, v: Any) -> Any:
        if v in (None, "") or is_unresolved_template(v):
            return []
        if isinstance(v, str):
            import json
            s = v.strip()
            if not s:
                return []
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
                return [s]
            except (json.JSONDecodeError, ValueError):
                return [s] if s and not is_unresolved_template(s) else []
        if isinstance(v, list):
            return [str(x) for x in v]
        return []

    @field_validator("party_size", mode="before")
    @classmethod
    def _coerce_party(cls, v: Any) -> Any:
        if v in (None, "") or is_unresolved_template(v):
            return None
        try:
            return int(float(str(v)))
        except (ValueError, TypeError):
            return None

    def resolved_call_id(self) -> str:
        return self.call_id or self.workflow_run_id

    def resolved_provider(self) -> str:
        p = clean_str(self.provider).lower() or "dograh"
        return p[:32]

    def resolved_provider_call_id(self) -> str:
        for cand in (self.provider_call_id, self.telephony_id,
                     self.telephony_call_id, self.call_sid):
            s = clean_str(cand)
            if s:
                return s[:128]
        return ""

    def _nested_customer(self, *keys: str) -> str:
        """First non-empty value for any key across the nested Dograh scopes.

        The ANI phone arrives as caller_number / from_number / msisdn inside
        initial_context; the name often sits in gathered_context. Top-level
        fields always win — this is only the fallback.
        """
        scopes = [self.customer or {}, self.gathered_context or {},
                  self.initial_context or {}]
        for scope in scopes:
            if not isinstance(scope, dict):
                continue
            lowered = {str(k).strip().lower(): v for k, v in scope.items()}
            for key in keys:
                v = scope.get(key, lowered.get(key.lower()))
                s = clean_str(v)
                if s:
                    return s
        return ""

    def resolved_customer_name(self) -> str:
        return (
            clean_str(self.customer_name)
            or clean_str(self.first_name)
            or self._nested_customer(
                "customer_name", "name", "full_name", "fullname",
                "first_name", "firstname", "caller_name", "callerName")
        )

    def resolved_customer_phone(self) -> str:
        return (
            clean_str(self.customer_phone)
            or clean_str(self.phone)
            or self._nested_customer(
                "customer_phone", "phone", "phone_number", "phoneNumber",
                "caller_number", "callerNumber", "from_number", "from",
                "msisdn", "ani", "caller_id", "callerId")
        )

    def resolved_first_name(self) -> str:
        return (
            clean_str(self.first_name)
            or self._nested_customer("first_name", "firstname", "name")
        ).split(" ")[0]

    def resolved_email(self) -> str:
        return (
            clean_str(self.email)
            or self._nested_customer("email", "customer_email", "customerEmail")
        )

    def resolved_duration(self) -> int:
        if self.duration_seconds:
            return self.duration_seconds
        if self.duration:
            return self.duration
        return int(self.cost_info.get("call_duration_seconds") or 0)

    def _transfer_evidence_in_contexts(self) -> str:
        """Transfer markers inside gathered/initial contexts (Dograh end-call).

        Run 1678 ended via end_call tool with Genesys session outputs
        {action: transfer, endReason: end_call_tool} — no transfer_reason
        field, so the row stayed 'completed'. Catch action/endReason-style
        markers here.
        """
        for scope in (self.gathered_context or {}, self.initial_context or {}):
            if not isinstance(scope, dict):
                continue
            for k, v in scope.items():
                ks = str(k).strip().lower()
                vs = str(v or "").strip().lower()
                if ks in ("transfer_requested", "transfer_requested_reason",
                           "transfer_reason", "reason_for_transfer",
                           "escalation_reason", "escalated", "handoff",
                           "handoff_requested") and vs:
                    return str(v)[:512]
                if ks in ("action", "endaction", "end_action", "endreason",
                           "end_reason", "call_action", "outcome", "call_outcome",
                           "disposition", "call_disposition",
                           "mapped_call_disposition") and any(
                        t in vs for t in ("transfer", "xfer", "escalat", "handoff", "agent")):
                    return f"{k}={v}"[:512]
                if ks in ("transfer", "transferred", "escalate") and vs not in (
                        "", "false", "0", "no", "none", "null"):
                    return f"{k}={v}"[:512]
        return ""

    def resolved_disposition(self) -> str:
        if self.transfer_requested or self.transfer_reason:
            return "transferred"
        if self._transfer_evidence_in_contexts():
            return "transferred"
        # Tool-call evidence (end_call with transfer action, transfer tool).
        for t in self.tool_calls or []:
            if not isinstance(t, dict):
                continue
            blob = f"{t.get('name', '')} {t.get('args_summary', '')} {t.get('result', '')}".lower()
            if any(k in blob for k in ("transfer", "xfer", "escalat", "handoff")):
                return "transferred"
        d = (self.disposition or "").strip().lower()
        # Dograh-side transfer ends the call with a transfer status — accept
        # every spelling ("Transfer", "transfer", "transferring", "transferred",
        # "warm transfer", "escalated", "escalation", "handoff to agent"...).
        if any(k in d for k in ("transfer", "xfer", "escalat", "handoff", "agent")):
            return "transferred"
        if not self.call_completed_successfully:
            return "failed"
        if "abandon" in (self.call_outcome or "").lower():
            return "abandoned"
        return d if d in (
            "completed", "transferred", "failed", "abandoned"
        ) else "completed"

    def resolved_recording_url(self) -> str:
        return self.recording_url or self.user_recording_url or self.bot_recording_url

    def resolved_purpose(self, transcript_text: str = "") -> str:
        """Best-effort purpose from webhook flags (before transcript AI runs).

        reservation_completed/confirmed → reservation; reservation/status-ish
        tool call → that purpose; creation evidence (completed/confirmed
        flags, items, total) → order_creation; transcript keywords as a
        last resort. Returns "" when unknown — callers must NOT fabricate
        "general" (the LLM classifier owns that label).

        NOTE: a bare echoed order_number alone is NOT creation evidence —
        status-check webhooks echo the number being asked about.
        """
        if self.reservation_completed or self.reservation_confirmed:
            return "reservation"
        for t in self.tool_calls or []:
            if isinstance(t, dict):
                n = str(t.get("name", "") or t.get("tool", "")).lower()
                if "reserv" in n or "book" in n or "table" in n:
                    return "reservation"
                if "status" in n or "track" in n or "lookup" in n:
                    return "order_status"
                if "order" in n or "create" in n:
                    return "order_creation"
        if self.order_completed or self.order_confirmed:
            return "order_creation"
        try:
            from app.routers.webhooks import _parse_items as _parse_webhook_items
            has_items = bool(_parse_webhook_items(self.order_items))
        except Exception:
            has_items = bool(self.order_items)
        if has_items or (self.total_price or 0):
            return "order_creation"
        kw = (transcript_text or "").lower()
        if kw:
            if any(k in kw for k in ("book a table", "reserve a table", "reservation",
                                     "table for ", "party of ", "party size")):
                return "reservation"
            if any(k in kw for k in ("order status", "status of my order",
                                     "where is my order", "is my order",
                                     "track my order", "cancel my order",
                                     "how long for my order")):
                return "order_status"
        return ""


class TransferWebhook(BaseModel):
    """Mid-call escalation handoff (Dograh -> app).

    Tolerates Dograh template spellings: every field has a default, stray
    spaces in keys are stripped, {{...}} unresolved literals become "",
    and reason accepts transfer_reason / transfer_requested_reason /
    reason_for_transfer / escalation_reason aliases. call_id accepts
    workflow_run_id alias.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    call_id: str = ""
    workflow_run_id: str = ""
    reason: str = ""
    transfer_reason: str = ""
    transfer_requested_reason: str = ""
    reason_for_transfer: str = ""
    escalation_reason: str = ""
    customer_name: Optional[str] = ""
    customer_phone: Optional[str] = ""
    phone: Optional[str] = ""

    @model_validator(mode="before")
    @classmethod
    def _map_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        stripped: dict[str, Any] = {}
        for k, v in data.items():
            sk = str(k).strip()
            if sk not in stripped:
                stripped[sk] = v
        data = stripped
        aliases = {
            "workflow run id": "call_id",
            "workflow_run_id": "call_id",
            "transfer reason": "reason",
            "transfer_reason": "reason",
            "transfer requested reason": "reason",
            "transfer_requested_reason": "reason",
            "reason for transfer": "reason",
            "reason_for_transfer": "reason",
            "escalation reason": "reason",
            "escalation_reason": "reason",
            "call outcome": "reason",
            "phone": "customer_phone",
            "phone number": "customer_phone",
            "customer phone number": "customer_phone",
            "customer name": "customer_name",
            "name": "customer_name",
            "first name": "customer_name",
        }
        normalized = {str(k).strip().lower().replace("-", " "): v for k, v in data.items()}
        for alias, canonical in aliases.items():
            if alias in normalized and not data.get(canonical):
                data[canonical] = normalized[alias]
        return data

    @field_validator("call_id", "workflow_run_id", "reason",
                     "transfer_reason", "transfer_requested_reason",
                     "reason_for_transfer", "escalation_reason",
                     "customer_name", "customer_phone", "phone", mode="before")
    @classmethod
    def _coerce_strs(cls, v: Any) -> Any:
        if v is None:
            return ""
        if is_unresolved_template(v):
            return ""
        s = str(v).strip()
        if s.startswith("<") and s.endswith(">"):
            return ""
        return s

    def resolved_call_id(self) -> str:
        return (self.call_id or self.workflow_run_id or "").strip()

    def resolved_reason(self) -> str:
        for cand in (self.reason, self.transfer_reason,
                     self.transfer_requested_reason, self.reason_for_transfer,
                     self.escalation_reason):
            s = clean_str(cand)
            if s:
                return s
        return ""

    def resolved_customer_phone(self) -> str:
        return clean_str(self.customer_phone or self.phone)

    def resolved_customer_name(self) -> str:
        return clean_str(self.customer_name)


class TransferContextResponse(BaseModel):
    call_id: str
    recorded: bool
    agent_context: dict[str, Any]


# ---------- Dashboard ----------

class CallOut(BaseModel):
    call_id: str
    call_type: str = ""
    call_purpose: str = ""
    disposition: str
    started_at: datetime
    duration_seconds: int
    customer_name: str
    customer_phone: str
    first_name: str = ""
    rsvp: str = ""
    summary: str
    escalation_reason: str
    order_numbers: list
    sentiment: str
    recording_url: str = ""
    transcript_url: str = ""
    provider: str = ""
    provider_call_id: str = ""

    class Config:
        from_attributes = True


class CallDetailOut(CallOut):
    transcript: list
    tool_calls: list
    cost: Optional[Decimal]
    recording_url: str
    ended_at: Optional[datetime]
    genesys_conversation_id: str
    user_recording_url: str = ""
    bot_recording_url: str = ""


class AnalyticsSummary(BaseModel):
    total_calls: int
    ai_resolved: int
    transferred: int
    failed: int
    orders_created: int
    revenue: Decimal
    avg_duration_seconds: int
    daily: list[dict]
