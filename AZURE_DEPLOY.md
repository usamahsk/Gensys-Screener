# Azure deployment (GHCR images + env, seed disabled)

## 1. Push images to GHCR (runs on push to main via `.github/workflows/deploy.yml`)

Images: `ghcr.io/usamahsk/kfc-api` and `ghcr.io/usamahsk/kfc-dashboard`

## 2. On the Azure VM / Container Apps host

```bash
# install docker compose, then:
cd kfc-app
cp .env.prod.example .env.prod   # edit secrets (KFC_DB_PASSWORD, etc.)
```

Edit `.env.prod` — key production values:

| Var | Purpose |
|-----|---------|
| `KFC_SEED_ENABLED=false` | **disables boot seeding** (prod stays clean) |
| `KFC_DB_PASSWORD` | strong postgres password |
| `KFC_GEMINI_API_KEY` | optional LLM summaries/embeddings |
| `NEXT_PUBLIC_API_URL` | `https://dsquare.larynxai.in` |
| `GHCR_OWNER`, `API_TAG`, `DASHBOARD_TAG` | image source |

## 3. Run

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

`docker-compose.prod.yml` pulls images from GHCR (no local build) and forces:
- `KFC_SEED_ENABLED: "false"` on api
- loopback-only ports (`127.0.0.1:8100`, `127.0.0.1:3100`)
- `restart: unless-stopped`

## 4. Azure Database for PostgreSQL

Point `KFC_DATABASE_URL` (composed from `POSTGRES_USER`/`KFC_DB_PASSWORD`/`POSTGRES_DB` + host `db`) at Azure Database for PostgreSQL with pgvector enabled if not using the compose `db` service. Seed stays off via `KFC_SEED_ENABLED=false`.
