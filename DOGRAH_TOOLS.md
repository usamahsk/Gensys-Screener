# Dograh Tool Configuration Guide

Configure these as **HTTP API tools** in your Dograh workflow. Each section gives
the Dograh fields, the sample request Dograh will send, and the response the
agent sees.

## ⚠️ Base URL — important

Dograh runs in Docker, so `localhost` from Dograh's container is NOT your machine.

| Where Dograh runs                          | Base URL to use                        |
|--------------------------------------------|----------------------------------------|
| Docker Desktop (Mac/Windows)               | `http://host.docker.internal:8100`     |
| Docker on Linux                            | `http://<your-machine-LAN-IP>:8100`    |
| Azure / deployed                           | `https://<kfc-api-container-app-url>`  |

Replace `<BASE>` below accordingly. All requests need header `Content-Type: application/json` (POST only).

---

## Tool 1 — `create_order`

**Method:** `POST`  **URL:** `<BASE>/api/tools/orders`

Header: `Content-Type: application/json`

Parameters:

| name           | type   | required | description for the LLM |
|----------------|--------|----------|--------------------------|
| customer_name  | string | yes      | Customer's full name, confirmed. Use this exact parameter name (snake_case). |
| customer_phone | string | yes      | Customer's phone digits. Use this exact parameter name — NOT "customer phone number". |
| order_type     | string | yes      | "takeaway" or "dinein" — confirm with customer first |
| table_number   | string | no       | Table number (dinein only) |
| pickup_time    | string | no       | Requested pickup time (takeaway only), e.g. "7:30 PM" |
| delivery_address | string | no     | Delivery address if the customer gives one, else omit entirely |
| payment_method | string | no       | Payment method if mentioned (cash/UPI/card), else omit entirely |
| email          | string | no       | Email if customer offers one, else omit entirely (do NOT send "none") |
| items          | array  | yes      | **Recorded verbatim — no menu validation.** Each entry {name: string, quantity: number, unit_price: number, size?: string, notes?: string}. Send the dish name as heard plus the price you quoted. Aliases accepted: `price` = unit_price, `item`/`dish`/`product` = name. |
| total          | number | no       | Order total fallback, used only when items carry no prices |
| call_id        | string | no       | Preset parameter `{{workflow_run_id}}` (see note below) |
| first_name     | string | no       | Preset `{{initial_context.first_name}}` — stored on the call log |
| rsvp           | string | no       | Preset `{{gathered_context.rsvp}}` — stored on the call log |
| duration       | number | no       | Preset `{{cost_info.call_duration_seconds}}` — stored on the call log |
| recording_url  | string | no       | Preset `{{recording_url}}` |
| user_recording_url | string | no   | Preset `{{user_recording_url}}` |
| bot_recording_url | string | no    | Preset `{{bot_recording_url}}` |
| transcript_url | string | no       | Preset `{{transcript_url}}` |

> **Important — link orders to their call:** add `call_id` as a **preset parameter**
> with value `{{workflow_run_id}}` (not an LLM parameter). Every create_order with
> a `call_id` also creates a **Call Logs** row of type `create_order` carrying all
> the preset fields above. The order dialog then shows "RELATED CALL" automatically.
> The post-call webhook also backfills `call_id` onto orders created without it.

Sample request (all fields):
```json
{
  "customer_name": "Rohan Mehta",
  "customer_phone": "+919812345670",
  "email": "rohan@example.com",
  "order_type": "takeaway",
  "pickup_time": "7:30 PM",
  "payment_method": "UPI",
  "items": [
    { "name": "Pepperoni Pizza Large", "quantity": 1, "unit_price": 749, "size": "Large" },
    { "name": "Coke Large", "quantity": 1, "unit_price": 99, "size": "Large" }
  ],
  "call_id": "{{workflow_run_id}}",
  "first_name": "{{initial_context.first_name}}",
  "rsvp": "{{gathered_context.rsvp}}",
  "duration": "{{cost_info.call_duration_seconds}}",
  "recording_url": "{{recording_url}}",
  "user_recording_url": "{{user_recording_url}}",
  "bot_recording_url": "{{bot_recording_url}}",
  "transcript_url": "{{transcript_url}}"
}
```

Sample response (201):
```json
{
  "order_number": "KFC-A1B2C3",
  "total": "998.00",
  "status": "confirmed",
  "items": [
    { "sku": "PZ-PEP-L", "name": "Pepperoni Pizza Large", "quantity": 1, "unit_price": "749.00", "size": "Large", "notes": "" }
  ]
}
```
Read back `order_number` and `total` to confirm.

## Tool 2 — `get_order_status`

**Method:** `GET`  **URL:** `<BASE>/api/tools/order-status?order_number={{order_number}}&call_id={{workflow_run_id}}`

Parameters (query):

| name         | type   | required | description for the LLM |
|--------------|--------|----------|--------------------------|
| order_number | string | yes      | The order number the customer provided, e.g. KFC-A1B2C3 |
| call_id | string | no | Preset `{{workflow_run_id}}` — logs a Call Logs row of type `order_status` |
| first_name | string | no | Preset `{{initial_context.first_name}}` |
| rsvp | string | no | Preset `{{gathered_context.rsvp}}` |
| duration | number | no | Preset `{{cost_info.call_duration_seconds}}` |
| recording_url | string | no | Preset `{{recording_url}}` |
| user_recording_url | string | no | Preset `{{user_recording_url}}` |
| bot_recording_url | string | no | Preset `{{bot_recording_url}}` |
| transcript_url | string | no | Preset `{{transcript_url}}` |

Sample response:
```json
{
  "found": true,
  "order": {
    "order_number": "KFC-A1B2C3",
    "status": "preparing",
    "order_type": "takeaway",
    "pickup_time": "7:30 PM",
    "total": "998.00",
    "items": [ { "name": "Family Fest Bucket", "quantity": 1 } ],
    "events": [ { "status": "confirmed", "created_at": "..." }, { "status": "preparing", "created_at": "..." } ]
  }
}
```
`found: false` → tell the customer the order number wasn't found, ask to re-check.
Convert status to natural speech: in_transit → "on its way", preparing → "being prepared".

## Tool 3 — `get_customer_memory`

**Method:** `GET`  **URL:** `<BASE>/api/tools/memory?phone={{phone}}`

Call this at the start of every call once the phone number is known (ANI) or
confirmed by the customer.

| name  | type   | required | description |
|-------|--------|----------|-------------|
| phone | string | yes      | Customer's phone number |

Sample response:
```json
{
  "phone": "+919812345670",
  "name": "Rohan Mehta",
  "last_intent": "order_placement",
  "last_order_number": "KFC-A1B2C3",
  "context": {},
  "recent_memories": [
    { "kind": "order", "text": "Placed order KFC-A1B2C3: 1x Family Fest Bucket...", "created_at": "..." },
    { "kind": "note", "text": "Prefers family buckets, usually orders for 4 people.", "created_at": "..." }
  ]
}
```
Use this to greet returning customers by name and continue where they left off —
never make them repeat their history.

## Tool 4 — `record_customer_memory`

**Method:** `POST`  **URL:** `<BASE>/api/tools/memory`

| name  | type   | required | description |
|-------|--------|----------|-------------|
| phone | string | yes      | Customer's phone number |
| text  | string | yes      | One-line note worth remembering: preferences, what they asked about, complaints |
| kind  | string | no       | "order" | "trace" | "note". Default "note" |

Sample request: `{ "phone": "+919812345670", "text": "Prefers spicy food, asked about Peri Peri", "kind": "note" }`
Response: `201` with the stored memory object.

---

## Post-call webhooks (not agent tools — configure in Dograh's post-call/webhook hook, or trigger at call end)

**Call ended** — `POST <BASE>/api/webhooks/call-ended`
```json
{
  "call_id": "{{workflow_run_id}}",
  "first_name": "{{initial_context.first_name}}",
  "rsvp": "{{gathered_context.rsvp}}",
  "duration": "{{cost_info.call_duration_seconds}}",
  "recording_url": "{{recording_url}}",
  "user_recording_url": "{{user_recording_url}}",
  "bot_recording_url": "{{bot_recording_url}}",
  "transcript_url": "{{transcript_url}}",
  "disposition": "completed",
  "customer_name": "Rohan Mehta",
  "customer_phone": "+919812345670",
  "transcript": [ { "role": "customer", "content": "..." }, { "role": "assistant", "content": "..." } ],
  "tool_calls": [ { "name": "create_order", "args_summary": "Pepperoni Large x1" } ],
  "order_numbers": ["KFC-A1B2C3"]
}
```
`disposition`: completed | transferred | failed | abandoned.

**Escalation/transfer** — `POST <BASE>/api/webhooks/transfer`
```json
{ "call_id": "run-abc123", "reason": "Customer reported a double charge", "customer_name": "Priya Sharma", "customer_phone": "+919812345671" }
```
Response includes `agent_context` — the summary + customer + last order that the
human agent sees on the dashboard's Agent View.

---

## Agent system prompt additions (KFC)

```
You are KFC's phone ordering assistant.
- Greet callers; after getting their phone number, call get_customer_memory and
  use it to greet returning customers by name and continue past conversations.
- Confirm item, quantity, and type (takeaway/dinein + table no. or pickup time)
  before calling create_order. Read back the order total and order number.
- For "where is my order" questions, call get_order_status and answer naturally.
- Requests about refunds, payments, complaints, or policy exceptions always go
  to a human.
```
