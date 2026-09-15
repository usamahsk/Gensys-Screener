"""Idempotent seed: sample customers/orders/calls.

Orders are self-contained (name/qty/price stored on the order itself) —
there is intentionally NO menu catalog; orders record whatever the voice
agent sends. Runs at startup only when tables are empty.
"""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Call, Customer, Memory, Order, OrderEvent, OrderItem
from app.services.embeddings import embed_one

SAMPLE_CALLS = [
    {
        "call_id": "call_seed_001",
        "disposition": "completed",
        "duration_seconds": 214,
        "customer_name": "Rohan Mehta",
        "customer_phone": "+919812345670",
        "summary": (
            "Customer wanted a large pepperoni pizza for a family dinner. AI confirmed size Large, "
            "added a large Coke, and placed takeaway order PB-A1B for $848 for pickup at 7:30 PM. "
            "Call completed successfully."
        ),
        "transcript": [
            {"role": "customer", "content": "Hi, I want a pepperoni pizza for my family."},
            {"role": "assistant", "content": "Great! Pepperoni comes in Regular $299, Medium $499 and Large $749. Which size?"},
            {"role": "customer", "content": "Large, and add a large Coke."},
            {"role": "assistant", "content": "Large Pepperoni $749 and Large Coke $99, total $848. Takeaway or dine-in?"},
            {"role": "customer", "content": "Takeaway, pickup around 7:30 PM. Rohan Mehta, 9812345670."},
            {"role": "assistant", "content": "Order placed! Your order number is PB-A1B. Pickup at 7:30 PM. Thank you!"},
        ],
        "tool_calls": [
            {"name": "create_order", "args_summary": "PZ-PEP-L, DRK-COK-L, takeaway 7:30 PM"},
        ],
        "order_numbers": ["PB-A1B"],
        "sentiment": "positive",
        "escalation_reason": "",
    },
    {
        "call_id": "call_seed_002",
        "disposition": "transferred",
        "duration_seconds": 331,
        "customer_name": "Priya Sharma",
        "customer_phone": "+919812345671",
        "summary": (
            "Customer checked order PB-D4E (In transit) and later reported she was double-charged "
            "on a previous online payment. Payment disputes are outside AI scope, so the call was "
            "transferred to a human agent with the full summary and order context."
        ),
        "transcript": [
            {"role": "customer", "content": "Where is my order PB-D4E?"},
            {"role": "assistant", "content": "Order PB-D4E is in transit, expected in 15 minutes."},
            {"role": "customer", "content": "Also, I was charged twice for my last online order. I need a refund."},
            {"role": "assistant", "content": "I'm sorry about that. Payment refunds need a human agent — connecting you now with full context so you don't have to repeat."},
        ],
        "tool_calls": [
            {"name": "order_status", "args_summary": "PB-D4E"},
            {"name": "transfer_to_agent", "args_summary": "payment dispute"},
        ],
        "order_numbers": ["PB-D4E"],
        "sentiment": "negative",
        "escalation_reason": "Payment issue: double charge reported, refund requested",
    },
    {
        "call_id": "call_seed_003",
        "disposition": "abandoned",
        "duration_seconds": 42,
        "customer_name": "",
        "customer_phone": "+919812345672",
        "summary": "Customer hung up while the AI was reading out today's deals. No order placed.",
        "transcript": [
            {"role": "customer", "content": "What deals do you have today?"},
            {"role": "assistant", "content": "Today we have the Krunch Combo at $249, Zinger Combo..."},
        ],
        "tool_calls": [],
        "order_numbers": [],
        "sentiment": "neutral",
        "escalation_reason": "",
    },
]

SAMPLE_ORDERS = [
    # (number, name, phone, email, type, table, pickup, status, items[(name, qty, price)], age_days)
    ("PB-A1B", "Rohan Mehta", "+919812345670", "", "takeaway", "", "7:30 PM", "confirmed",
     [("Pepperoni Pizza Large", 1, 749), ("Coke Large", 1, 99)], 0),
    ("PB-D4E", "Priya Sharma", "+919812345671", "priya@example.com", "takeaway", "", "", "in_transit",
     [("Margherita Pizza Medium", 1, 349), ("Coke Regular", 1, 60)], 0),
    ("PB-G7H", "Arjun Verma", "+919812345673", "", "dinein", "12", "", "completed",
     [("Pepperoni Pizza Regular", 1, 299), ("Coke Regular", 2, 60)], 1),
    ("PB-J1K", "Sneha Iyer", "+919812345674", "", "takeaway", "", "8:00 PM", "preparing",
     [("Margherita Pizza Large", 1, 549), ("Coke Regular", 1, 60)], 0),
    ("PB-M4N", "Vikram Singh", "+919812345675", "", "dinein", "4", "", "completed",
     [("Pepperoni Pizza Medium", 1, 499), ("Coke Large", 1, 99)], 2),
    ("PB-P7Q", "Rohan Mehta", "+919812345670", "", "takeaway", "", "", "cancelled",
     [("Margherita Pizza Regular", 2, 199)], 3),
]


async def seed_if_empty(db: AsyncSession) -> dict:
    counts = {}
    order_count = (await db.execute(select(func.count(Order.id)))).scalar() or 0
    if order_count == 0:
        now = utcnow()
        for (num, name, phone, email, otype, table, pickup, status, items, age_days) in SAMPLE_ORDERS:
            created = now - timedelta(days=age_days, hours=2)
            order = Order(order_number=num, customer_name=name, customer_phone=phone,
                          email=email, order_type=otype, table_number=table,
                          pickup_time=pickup, status=status, source="voice_agent",
                          call_id="call_seed_001" if num == "PB-A1B" else "",
                          created_at=created, updated_at=created)
            db.add(order)
            await db.flush()
            total = 0.0
            for item_name, qty, price in items:
                total += float(price) * qty
                db.add(OrderItem(order_id=order.id, menu_item_id=None, sku="",
                                 name=item_name, quantity=qty, unit_price=Decimal(price), size=""))
            order.total = Decimal(round(total, 2))
            timeline = ["confirmed", "preparing"]
            if status in ("in_transit", "out_for_delivery"):
                timeline += ["ready"]
            if status == "completed":
                timeline += ["ready", "completed"]
            if status == "cancelled":
                timeline += ["cancelled"]
            for i, st in enumerate(timeline):
                db.add(OrderEvent(order_id=order.id, status=st,
                                  created_at=created + timedelta(minutes=10 * (i + 1))))
        counts["orders"] = len(SAMPLE_ORDERS)

    cust_count = (await db.execute(select(func.count(Customer.id)))).scalar() or 0
    if cust_count == 0:
        customers = [
            ("+919812345670", "Rohan Mehta", "order_placement", "PB-A1B",
             ["Placed order PB-A1B: Large Pepperoni Pizza + Large Coke, total $848, takeaway pickup 7:30 PM.",
              "Prefers pepperoni, usually orders large for family nights."]),
            ("+919812345671", "Priya Sharma", "support", "PB-D4E",
             ["Checked order PB-D4E (in transit). Later reported a double-charge payment issue and was transferred.",
              "Has an open payment dispute - handle with care."]),
        ]
        for phone, name, intent, last_order, notes in customers:
            cust = Customer(phone=phone, name=name, last_intent=intent, last_order_number=last_order)
            db.add(cust)
            await db.flush()
            for note in notes:
                vec = await embed_one(note)
                db.add(Memory(customer_id=cust.id, kind="note", text=note, embedding=vec))
        counts["customers"] = len(customers)

    call_count = (await db.execute(select(func.count(Call.id)))).scalar() or 0
    if call_count == 0:
        for c in SAMPLE_CALLS:
            db.add(Call(
                call_id=c["call_id"], disposition=c["disposition"],
                started_at=utcnow() - timedelta(seconds=c["duration_seconds"] + 3600),
                ended_at=utcnow() - timedelta(seconds=3600),
                duration_seconds=c["duration_seconds"],
                customer_name=c["customer_name"], customer_phone=c["customer_phone"],
                transcript=c["transcript"], tool_calls=c["tool_calls"],
                order_numbers=c["order_numbers"], summary=c["summary"],
                escalation_reason=c["escalation_reason"], sentiment=c["sentiment"],
                cost=round(c["duration_seconds"] * 0.0032, 4),
            ))
        counts["calls"] = len(SAMPLE_CALLS)

    if counts:
        await db.commit()
    return counts


def utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
