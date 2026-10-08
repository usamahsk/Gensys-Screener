from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from app.config import get_settings
from app.db import engine
from app.models import Base
from app.routers import analytics, auth, calls, integration, orders, reports, telephony, tools, transactions, webhooks

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight forward-migration for existing DBs (create_all won't
        # add columns to tables that already exist).
        from sqlalchemy import text as _text
        for _stmt in (
            "ALTER TABLE calls ADD COLUMN IF NOT EXISTS provider VARCHAR(32) DEFAULT 'dograh'",
            "ALTER TABLE calls ADD COLUMN IF NOT EXISTS provider_call_id VARCHAR(128) DEFAULT ''",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS provider VARCHAR(32) DEFAULT 'dograh'",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS provider_call_id VARCHAR(128) DEFAULT ''",
            "ALTER TABLE calls ADD COLUMN IF NOT EXISTS call_purpose VARCHAR(32) DEFAULT ''",
            "CREATE INDEX IF NOT EXISTS ix_calls_provider_call_id ON calls (provider_call_id)",
            "CREATE INDEX IF NOT EXISTS ix_orders_provider_call_id ON orders (provider_call_id)",
            "CREATE INDEX IF NOT EXISTS ix_calls_call_purpose ON calls (call_purpose)",
            "ALTER TABLE calls ADD COLUMN IF NOT EXISTS initial_context JSONB DEFAULT '{}'",
            "ALTER TABLE calls ADD COLUMN IF NOT EXISTS gathered_context JSONB DEFAULT '{}'",
            "ALTER TABLE calls ADD COLUMN IF NOT EXISTS customer_intent VARCHAR(64) DEFAULT ''",
        ):
            try:
                await conn.execute(_text(_stmt))
            except Exception as e:
                logger.warning(f"migration skipped ({_stmt[:60]}…): {e}")
    if not settings.seed_enabled:
        logger.info("Boot seeding disabled (KFC_SEED_ENABLED=false) — starting with existing data only")
        yield
        return
    from app.db import SessionLocal

    from app.seed_data import seed_if_empty

    async with SessionLocal() as db:
        seeded = await seed_if_empty(db)
        if seeded:
            logger.info(f"Seeded database: {seeded}")
    yield


app = FastAPI(title="Pizza Bay Voice Commerce API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(tools.router)
app.include_router(webhooks.router)
app.include_router(orders.router)
app.include_router(calls.router)
app.include_router(integration.router)
app.include_router(integration.google_router)
app.include_router(integration.langfuse_router)
app.include_router(analytics.router)
app.include_router(reports.router)
app.include_router(transactions.router)
app.include_router(telephony.router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Log exactly what the voice agent sent and why it failed — so a 422 is debuggable.

    Shows in `docker logs` / uvicorn stdout. The response body also carries the
    details so ngrok testers see them without server access.
    """
    try:
        raw = (await request.body()).decode("utf-8", errors="replace")[:4000]
    except Exception:
        raw = "<unreadable body>"
    errors = exc.errors()
    logger.error(f"422 on {request.method} {request.url.path} | body={raw} | errors={errors}")
    return JSONResponse(
        status_code=422,
        content={
            "detail": errors,
            "hint": "Check server logs for '422 on ...' with the raw body. "
                    "Common causes: items as '{{...}}' string, empty customer_phone, "
                    "order_type other than takeaway/dinein.",
            "received_body": raw,
        },
    )


@app.get("/api/health")
async def health():
    return {"status": "ok"}
