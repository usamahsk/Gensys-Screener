import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import get_settings

settings = get_settings()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class MenuItem(Base):
    __tablename__ = "menu_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(64), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    size: Mapped[Optional[str]] = mapped_column(String(32), default="")
    spice_level: Mapped[Optional[str]] = mapped_column(String(16), default="")
    is_veg: Mapped[bool] = mapped_column(Boolean, default=False)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    tags: Mapped[list] = mapped_column(JSONB, default=list)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(128), default="")
    email: Mapped[Optional[str]] = mapped_column(String(128), default="")
    last_intent: Mapped[Optional[str]] = mapped_column(String(64), default="")
    last_order_number: Mapped[Optional[str]] = mapped_column(String(32), default="")
    context: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    memories: Mapped[list["Memory"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )


class Memory(Base):
    """Vectorized customer history so returning callers continue, not restart."""

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="note")  # order|trace|note|call_summary
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(Vector(settings.embedding_dim))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    customer: Mapped[Customer] = relationship(back_populates="memories")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    customer_name: Mapped[str] = mapped_column(String(128), default="")
    customer_phone: Mapped[str] = mapped_column(String(32), default="", index=True)
    email: Mapped[Optional[str]] = mapped_column(String(128), default="")
    order_type: Mapped[str] = mapped_column(String(16), default="takeaway")  # takeaway|dinein
    table_number: Mapped[Optional[str]] = mapped_column(String(16), default="")
    pickup_time: Mapped[Optional[str]] = mapped_column(String(32), default="")
    delivery_address: Mapped[Optional[str]] = mapped_column(String(512), default="")
    payment_method: Mapped[Optional[str]] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(24), default="confirmed", index=True)
    # confirmed -> preparing -> ready -> completed | cancelled
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    source: Mapped[str] = mapped_column(String(24), default="voice_agent")
    call_id: Mapped[Optional[str]] = mapped_column(String(64), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    events: Mapped[list["OrderEvent"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderEvent.created_at"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    menu_item_id: Mapped[Optional[int]] = mapped_column(ForeignKey("menu_items.id"))
    sku: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(128))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    size: Mapped[Optional[str]] = mapped_column(String(32), default="")
    notes: Mapped[Optional[str]] = mapped_column(String(256), default="")

    order: Mapped[Order] = relationship(back_populates="items")


class OrderEvent(Base):
    __tablename__ = "order_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    status: Mapped[str] = mapped_column(String(24))
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    order: Mapped[Order] = relationship(back_populates="events")


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    direction: Mapped[str] = mapped_column(String(16), default="inbound")
    disposition: Mapped[str] = mapped_column(String(24), default="completed")
    # completed | transferred | failed | abandoned
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    customer_name: Mapped[Optional[str]] = mapped_column(String(128), default="")
    customer_phone: Mapped[Optional[str]] = mapped_column(String(32), default="", index=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(128), default="")
    rsvp: Mapped[Optional[str]] = mapped_column(String(256), default="")
    transcript: Mapped[list] = mapped_column(JSONB, default=list)
    tool_calls: Mapped[list] = mapped_column(JSONB, default=list)
    order_numbers: Mapped[list] = mapped_column(JSONB, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    escalation_reason: Mapped[Optional[str]] = mapped_column(String(512), default="")
    sentiment: Mapped[Optional[str]] = mapped_column(String(16), default="")
    cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    recording_url: Mapped[Optional[str]] = mapped_column(String(512), default="")
    transcript_url: Mapped[Optional[str]] = mapped_column(String(512), default="")
    user_recording_url: Mapped[Optional[str]] = mapped_column(String(512), default="")
    bot_recording_url: Mapped[Optional[str]] = mapped_column(String(512), default="")
    genesys_conversation_id: Mapped[Optional[str]] = mapped_column(String(64), default="")
    # Link back to the voice platform run (populated on import; webhook path keeps UUID call_id)
    workflow_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    run_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    workflow_name: Mapped[Optional[str]] = mapped_column(String(128), default="")
    # create_order | order_status (tool call logs) | inbound | webhook | ...
    call_type: Mapped[Optional[str]] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Faq(Base):
    __tablename__ = "faqs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    category: Mapped[Optional[str]] = mapped_column(String(64), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    embedding: Mapped[Any] = mapped_column(Vector(settings.embedding_dim))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class DograhSetting(Base):
    """Single-row (id=1) Dograh integration config: API key + base URL."""

    __tablename__ = "dograh_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_key: Mapped[str] = mapped_column(Text, default="")
    base_url: Mapped[str] = mapped_column(String(256), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class GoogleSetting(Base):
    """Single-row (id=1) Google LLM config, managed from the dashboard.

    Used by transcript-based order recovery: when a post-call webhook arrives
    without usable order data, the saved transcript is sent to Gemini which
    returns order JSON matching our payload, and the order is created/updated.
    Falls back to KFC_GEMINI_API_KEY env when no dashboard key is set.
    """

    __tablename__ = "google_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_key: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(128), default="gemini-2.0-flash")
    recovery_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    use_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class LangfuseSetting(Base):
    """Single-row (id=1) Langfuse observability config, managed from the dashboard.

    Org-level: one key trio for the whole Dograh instance. Saved locally and
    pushed to the Dograh org model-config so traces keep flowing without
    opening Dograh. The dashboard then deep-links each run/call to its
    Langfuse trace.
    """

    __tablename__ = "langfuse_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_key: Mapped[str] = mapped_column(Text, default="")
    secret_key: Mapped[str] = mapped_column(Text, default="")
    host: Mapped[str] = mapped_column(String(256), default="https://cloud.langfuse.com")
    project_id: Mapped[str] = mapped_column(String(128), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
