from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from app.config import get_settings
from app.db import engine
from app.models import Base
from app.routers import analytics, calls, integration, orders, tools, webhooks

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from app.db import SessionLocal

    from app.seed_data import seed_if_empty

    async with SessionLocal() as db:
        seeded = await seed_if_empty(db)
        if seeded:
            logger.info(f"Seeded database: {seeded}")
    yield


app = FastAPI(title="KFC Voice Commerce API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tools.router)
app.include_router(webhooks.router)
app.include_router(orders.router)
app.include_router(calls.router)
app.include_router(integration.router)
app.include_router(integration.google_router)
app.include_router(analytics.router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Log exactly what Dograh sent and why it failed — so a 422 is debuggable.

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
