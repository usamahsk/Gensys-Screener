# Pizza Bay Voice AI — Ordering Assistant POC

Voice AI ordering assistant for Pizza Bay (takeaway / dine-in) built on **Dograh**, with an
operations dashboard. Phase 2 **Genesys Cloud** integration is wired but dormant — it
activates when credentials are configured (no code changes needed).

```
kfc-app/
├── backend/    FastAPI + Postgres/pgvector  → :8100
├── frontend/   Next.js 16 + shadcn/ui       → :3100
├── dograh-claude/  Dograh voice AI platform (upstream repo)
└── docker-compose.yml
```

## Run (Docker)

```bash
cd kfc-app
docker compose up -d --build
```

| Service   | URL                          |
|-----------|------------------------------|
| Dashboard | http://localhost:3100        |
| API       | http://localhost:8100/api/health |
| Swagger   | http://localhost:8100/docs   |

The database seeds itself on first boot: Pizza Bay menu (25 items), 15 FAQs (embedded),
sample orders, customers and calls — so the dashboard is usable immediately.

Optional: set `KFC_GEMINI_API_KEY` on the `api` service for LLM-quality call
summaries + embeddings. Without it everything still works offline (local
embeddings + extractive summaries).

## Dashboard

- **Overview** — call volume, AI resolution, transfers, revenue (14-day chart)
- **Orders** — all voice-agent orders with status + tracking timeline
- **Call Logs** — transcript, tool calls, AI summary, cost, per call
- **Agent View** — escalated calls: reason for transfer + AI summary + customer
  context + related orders (the "logger" the human agent sees before pickup)
- **Knowledge Base** — add/edit/deactivate FAQs; re-embedded into the vector
  index on every save

## Voice agent wiring (Dograh side)

Configure these as HTTP tools / actions in the Dograh workflow:

| Action          | Call                                                             |
|-----------------|------------------------------------------------------------------|
| Menu search     | `POST /api/tools/menu/search` `{query, category?, max_price?, is_veg?}` |
| Create order    | `POST /api/tools/orders` `{customer_name, customer_phone, order_type, table_number?/pickup_time?, items:[{sku, quantity}]}` |
| Order status    | `GET  /api/tools/orders/{ORDER_NUMBER}`                          |
| FAQ answer      | `POST /api/tools/faq/query` `{question}` → returns `can_answer` (false = escalate, don't hallucinate) |
| Customer memory | `GET/POST /api/tools/memory/{phone}` (continuity across calls)   |
| Post-call log   | `POST /api/webhooks/call-ended` (transcript → stored + summarized) |
| Escalation      | `POST /api/webhooks/transfer` `{call_id, reason}` → returns the agent context payload |

Agent prompt guidance: when `faq_query` returns `can_answer: false`, say you'll
connect a human and trigger transfer — do not guess.

## Genesys Cloud (Phase 2 — dormant until configured)

1. In the Genesys admin console create an **Audio Connector / AudioHook**
   integration pointing to the Dograh deployment, with the shared API key +
   client secret.
2. In Dograh's telephony settings add the **Genesys** provider config
   (api_key, client_secret, OAuth client id/secret, region e.g.
   `usw2.pure.cloud`).
3. Escalations use Dograh's built-in Genesys transfer strategy + participant
   attribute push (`session_logger`), so the Genesys flow/queue receives the
   AI summary and context. This app's Agent View mirrors the same context.

## Azure Container Apps

Each compose service maps to one container app (`db`, `api`, `dashboard`).
Build images with:

```bash
docker build -t <registry>/kfc-api ./backend
docker build -t <registry>/kfc-dashboard --build-arg NEXT_PUBLIC_API_URL=https://<api-url> ./frontend
```

`NEXT_PUBLIC_API_URL` is a build arg — set it to the public API URL. Point the
api app at Azure Database for PostgreSQL (pgvector extension enabled).
# Gensys-Screener
