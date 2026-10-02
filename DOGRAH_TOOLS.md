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
| Deployed (dsquare)                         | `https://dsquare.larynxai.in`          |
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
| provider       | string | no       | Preset `dograh` (or `genesys`) — stored as the provider column |
| provider_call_id | string | no     | Preset telephony/SIP Call SID from your telephony provider (Twilio/Vonage/Plivo/Telnyx) — stored as the telephony ID, the Dograh flavour of the Genesys conversation id. Aliases accepted: `telephony_id`, `telephony_call_id`, `call_sid` |
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
  "provider": "dograh",
  "provider_call_id": "{{initial_context.telephony_call_id}}",
  "first_name": "{{initial_context.first_name}}",
  "rsvp": "{{gathered_context.rsvp}}",
  "duration": "{{cost_info.call_duration_seconds}}",
  "recording_url": "{{recording_url}}",
  "user_recording_url": "{{user_recording_url}}",
  "bot_recording_url": "{{bot_recording_url}}",
  "transcript_url": "{{transcript_url}}"
}
```

> **Telephony ID:** `provider_call_id` accepts any provider-side call SID
> (Twilio/Vonage/Plivo/Telnyx). If your telephony setup exposes it under a
> different context key, point the preset at that key instead — e.g.
> `{{initial_context.call_sid}}`. Aliases `telephony_id`, `telephony_call_id`,
> `call_sid` all work. This is the Dograh equivalent of the Genesys
> conversation id, and it shows up as **Provider / Telephony ID** columns in
> Runs, Orders and Call Logs, plus the Excel reports.

Sample response (201):
```json
{
  "order_number": "PB-A1B",
  "total": "998.00",
  "status": "confirmed",
  "items": [
    { "sku": "PZ-PEP-L", "name": "Pepperoni Pizza Large", "quantity": 1, "unit_price": "749.00", "size": "Large", "notes": "" }
  ]
}
```
Read back `order_number` and `total` to confirm.

> **Pending response — never invent an order number:** if the tool call is
> missing items/phone, the response is
> `{ "order_number": "", "total": "0", "status": "pending", "items": [],
> "message": "Order noted but incomplete (missing: ...)..." }`.
> When `order_number` is empty, **do NOT read out any order number** — none
> was created. Collect the missing details and call `create_order` again
> with the full items list (names as heard, quantities, quoted prices).
> Always send the complete items array on every call — never an empty one.

## Tool 2 — `get_order_status` (POST — recommended)

**Method:** `POST`  **URL:** `<BASE>/api/tools/order-status`

Header: `Content-Type: application/json`

Use POST so a missing/null `order_number` never fails: when the LLM sends
`null`/empty, the endpoint resolves the correct order from `call_id`
(latest order on this call / call-log order list) or `customer_phone`
(latest order for that phone) and returns it. GET variants below still work.

Parameters:

| name         | type   | required | description for the LLM |
|--------------|--------|----------|--------------------------|
| order_number | string | no       | The order number the customer provided, e.g. PB-A1B. Send null/omit if not given — it will be resolved from the call. |
| call_id | string | no | Preset `{{workflow_run_id}}` — logs a Call Logs row of type `order_status` and enables fallback resolution |
| customer_phone | string | no | Preset/customer phone — fallback resolution when order_number is missing |
| phone | string | no | Alias of customer_phone |
| provider | string | no | Preset `dograh` |
| provider_call_id | string | no | Preset telephony/SIP Call SID |
| first_name | string | no | Preset `{{initial_context.first_name}}` |
| rsvp | string | no | Preset `{{gathered_context.rsvp}}` |
| duration | number | no | Preset `{{cost_info.call_duration_seconds}}` |
| recording_url | string | no | Preset `{{recording_url}}` |
| user_recording_url | string | no | Preset `{{user_recording_url}}` |
| bot_recording_url | string | no | Preset `{{bot_recording_url}}` |
| transcript_url | string | no | Preset `{{transcript_url}}` |
| initial_context | object | no | Preset `{{initial_context}}` — stored on the call log |
| gathered_context | object | no | Preset `{{gathered_context}}` — stored on the call log |
| customer_intent | string | no | Captured intent, e.g. order_status_check — stored on the call log |

Sample request (order_number unknown → resolved from the call):
```json
{
  "order_number": null,
  "call_id": "{{workflow_run_id}}",
  "customer_phone": "{{initial_context.phone}}",
  "provider": "dograh",
  "provider_call_id": "{{initial_context.telephony_call_id}}",
  "first_name": "{{initial_context.first_name}}",
  "rsvp": "{{gathered_context.rsvp}}",
  "duration": "{{cost_info.call_duration_seconds}}",
  "recording_url": "{{recording_url}}",
  "transcript_url": "{{transcript_url}}"
}
```

Sample response (resolved via call):
```json
{
  "found": true,
  "order": { "order_number": "PB-A1B", "status": "preparing", "...": "..." },
  "requested_order_number": "",
  "resolved_order_number": "PB-A1B",
  "lookup_method": "via_call_id"
}
```
`lookup_method`: exact | via_call_id | via_call_log | via_phone | none.
`found: false` → tell the customer the order number wasn't found, ask to re-check.

## Tool 2 (legacy GET variants — still supported)

**GET:** `<BASE>/api/tools/order-status?order_number={{order_number}}&call_id={{workflow_run_id}}`
or `<BASE>/api/tools/orders/{{order_number}}?call_id={{workflow_run_id}}`

Parameters (query):

| name         | type   | required | description for the LLM |
|--------------|--------|----------|--------------------------|
| order_number | string | yes      | The order number the customer provided, e.g. PB-A1B |
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
    "order_number": "PB-A1B",
    "customer_name": "Rohan Mehta",
    "customer_phone": "+919812345670",
    "status": "preparing",
    "order_type": "takeaway",
    "pickup_time": "7:30 PM",
    "delivery_address": "",
    "payment_method": "UPI",
    "total": "998.00",
    "items": [ { "name": "Family Fest Bucket", "quantity": 1, "unit_price": "998.00", "size": "", "notes": "" } ],
    "events": [ { "status": "confirmed", "created_at": "..." }, { "status": "preparing", "created_at": "..." } ]
  }
}
```
`found: false` → tell the customer the order number wasn't found, ask to re-check.
Convert status to natural speech: in_transit → "on its way", preparing → "being prepared".
Read back the whole order: items with quantities and sizes, total, pickup time or
delivery address, and payment method — all of these are in the response.

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
  "last_order_number": "PB-A1B",
  "context": {},
  "recent_memories": [
    { "kind": "order", "text": "Placed order PB-A1B: 1x Family Fest Bucket...", "created_at": "..." },
    { "kind": "note", "text": "Prefers family buckets, usually orders for 4 people.", "created_at": "..." }
  ]
}
```
Use this to greet returning customers by name and continue where they left off —
never make them repeat their history.

## Tool 4 — `record_customer_memory`

**Method:** `POST`  **URL:** `<BASE>/api/tools/memory/{{phone}}` (phone goes in the
path — there is no body-only variant)

Header: `Content-Type: application/json`

| name  | type   | required | description |
|-------|--------|----------|-------------|
| text  | string | yes      | One-line note worth remembering: preferences, what they asked about, complaints |
| kind  | string | no       | "order" \| "trace" \| "note" \| "call_summary". Default "note" |

Sample request to `<BASE>/api/tools/memory/+919812345670`:
`{ "text": "Prefers spicy food, asked about Peri Peri", "kind": "note" }`
Response: `201` with the stored memory object.

---

## Next order number (sequential)

**Method:** `GET`  **URL:** `<BASE>/api/orders/next-number`

Response: `{ "total_orders": 1381, "next_order_number": 1382 }`

Call this before `create_order` and use `next_order_number` as the order
number so numbering stays sequential (total count + 1).

## Post-call webhooks (not agent tools — configure in Dograh's post-call/webhook hook, or trigger at call end)

**Call ended** — `POST <BASE>/api/webhooks/call-ended`
```json
{
  "call_id": "{{workflow_run_id}}",
  "provider": "dograh",
  "provider_call_id": "{{initial_context.telephony_call_id}}",
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
  "order_numbers": ["PB-A1B"]
}
```
`disposition`: completed | transferred | failed | abandoned.

> **Empty customer on transactions?** The app reads `customer_name` /
> `customer_phone` (or `phone`) top-level first, then falls back to nested
> `gathered_context` / `initial_context` / `customer` objects (`customer_name`,
> `phone_number`, `caller_number`, `from_number`, `msisdn`, ...). If your
> template sends none of these, the call still saves (transcript → order
> extraction) but the customer stays blank. Easiest fix: pass the whole
> contexts through, e.g. `"gathered_context": "{{gathered_context}}"` and
> `"initial_context": "{{initial_context}}"`. For old rows, hit
> **Backfill missing data** on the Transactions page — it pulls the ANI
> phone/name out of the voice-platform run.

> **When does call-ended fire?** Only when the Dograh workflow has this URL
> configured as its post-call webhook — it then fires after EVERY call end,
> including caller hangups ("call cut"). If no call row appears at all, the
> webhook URL is missing/misconfigured on the Dograh side.
>
> **Cut calls with no order?** A hangup webhook usually carries no order
> fields and no transcript. The app then recovers automatically: first from
> the webhook transcript, else from the voice-platform run transcript (needs
> the Dograh API key in Settings), extracted via Gemini (needs the Google key
> in Settings with recovery enabled). If both keys are set and still no
> order, check the api logs — the transcript was empty or had no order in it.

**Escalation/transfer** — `POST <BASE>/api/webhooks/transfer`
```json
{ "call_id": "run-abc123", "reason": "Customer reported a double charge", "customer_name": "Priya Sharma", "customer_phone": "+919812345671" }
```
Response includes `agent_context` — the summary + customer + last order that the
human agent sees on the dashboard's Agent View.

> **Transfer from the Dograh side:** when the voice agent transfers, the call
> ends with a transfer status — send `POST <BASE>/api/webhooks/call-ended`
> with `"disposition": "transfer"` (any of transfer / transferred /
> escalated / handoff-to-agent) plus `transfer_reason` and the transcript.
> That call appears in the dashboard's **Agent View** (`?disposition=transferred`)
> with the transfer reason, the AI conversation summary, and the transcript
> tail so the human agent can continue the call without asking the caller to
> repeat themselves. Calling `/transfer` mid-call is optional but recommended:
> it pins the handoff in Agent View immediately, and the later `call-ended`
> fills in the full summary.

---

## Agent system prompt additions (Pizza Bay)

```
You are Pizza Bay's phone ordering assistant.
- Greet callers; after getting their phone number, call get_customer_memory and
  use it to greet returning customers by name and continue past conversations.
- Confirm item, quantity, and type (takeaway/dinein + table no. or pickup time)
  before calling create_order. Read back the order total and order number.
  Order numbers look like PB-X7K (PB- plus 3 characters) — read them back
  letter by letter. If create_order returns an empty order_number (pending),
  NEVER invent one — ask for the missing details and retry the tool.
- For "where is my order" questions, call get_order_status and answer naturally.
- Requests about refunds, payments, complaints, or policy exceptions always go
  to a human.
```
