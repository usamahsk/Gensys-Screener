"""Voice platform integration: settings + proxied runs/transcripts/recordings.

Allows the dashboard to pull calls directly from a voice platform instance using a
user-managed API key (X-API-Key), and to import a run into the Call Logs +
Orders so the analytics populate even without the post-call webhook.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Call, Customer, DograhSetting, GoogleSetting, Memory, Order, OrderEvent, OrderItem
from app.services import dograh_client

router = APIRouter(prefix="/api/integration/dograh", tags=["integration"])
google_router = APIRouter(prefix="/api/integration/google", tags=["integration"])

DEFAULT_BASE_URL = dograh_client.DEFAULT_BASE_URL


class DograhSettingRequest(BaseModel):
    api_key: Optional[str] = None
    base_url: Optional[str] = None


class DograhSettingResponse(BaseModel):
    base_url: str
    api_key_masked: str
    has_key: bool


class GoogleSettingRequest(BaseModel):
    api_key: Optional[str] = None
    model: Optional[str] = None
    recovery_enabled: Optional[bool] = None
    use_audio: Optional[bool] = None


class GoogleSettingResponse(BaseModel):
    model: str
    api_key_masked: str
    has_key: bool
    recovery_enabled: bool
    use_audio: bool


class DograhRun(BaseModel):
    run_id: int
    workflow_id: int
    workflow_name: Optional[str] = None
    name: Optional[str] = None
    created_at: str = ""
    duration_seconds: int = 0
    disposition: Optional[str] = None
    call_type: Optional[str] = None
    phone_number: Optional[str] = None
    charge_usd: Optional[float] = None
    transcript_url: Optional[str] = None
    recording_url: Optional[str] = None
    user_recording_url: Optional[str] = None
    bot_recording_url: Optional[str] = None
    transcript_public_url: Optional[str] = None
    recording_public_url: Optional[str] = None
    gathered_context: Optional[dict] = None


async def _get_setting(db: AsyncSession) -> DograhSetting:
    row = (await db.execute(select(DograhSetting).where(DograhSetting.id == 1))).scalar_one_or_none()
    if not row:
        row = DograhSetting(id=1, api_key="", base_url=DEFAULT_BASE_URL)
        db.add(row)
        await db.flush()
    return row


def _mask(key: str) -> str:
    if not key:
        return ""
    return key[:6] + "…" + key[-4:] if len(key) > 10 else "••••"


@router.get("", response_model=DograhSettingResponse)
async def get_dograh_setting(db: AsyncSession = Depends(get_db)):
    s = await _get_setting(db)
    return DograhSettingResponse(
        base_url=s.base_url or DEFAULT_BASE_URL,
        api_key_masked=_mask(s.api_key),
        has_key=bool(s.api_key),
    )


@router.put("", response_model=DograhSettingResponse)
async def update_dograh_setting(req: DograhSettingRequest, db: AsyncSession = Depends(get_db)):
    s = await _get_setting(db)
    if req.base_url is not None:
        s.base_url = req.base_url.strip() or DEFAULT_BASE_URL
    if req.api_key:
        s.api_key = req.api_key.strip()
    await db.commit()
    await db.refresh(s)
    return DograhSettingResponse(
        base_url=s.base_url or DEFAULT_BASE_URL,
        api_key_masked=_mask(s.api_key),
        has_key=bool(s.api_key),
    )


@router.get("/health")
async def dograh_health(db: AsyncSession = Depends(get_db)):
    s = await _get_setting(db)
    if not s.api_key:
        raise HTTPException(400, "No voice platform API key configured")
    return await dograh_client.dograh_health_check(s.api_key, s.base_url)


@router.get("/models", response_model=dict)
async def get_dograh_models(db: AsyncSession = Depends(get_db)):
    """Current org-level AI model configuration (provider keys are masked)."""
    s = await _get_setting(db)
    if not s.api_key:
        raise HTTPException(400, "No voice platform API key configured")
    try:
        return await dograh_client.get_model_config(s.api_key, s.base_url)
    except Exception as e:
        raise HTTPException(502, f"Voice platform request failed: {e}")


@router.put("/models", response_model=dict)
async def save_dograh_models(payload: dict, db: AsyncSession = Depends(get_db)):
    """Save org-level AI model configuration (BYOK providers + keys, or
    platform-managed). Masked keys sent back are preserved by the voice platform."""
    s = await _get_setting(db)
    if not s.api_key:
        raise HTTPException(400, "No voice platform API key configured")
    try:
        result = await dograh_client.save_model_config(s.api_key, s.base_url, payload)
    except Exception as e:
        raise HTTPException(502, f"Voice platform request failed: {e}")
    if result["status"] >= 400:
        raise HTTPException(result["status"], f"Voice platform rejected the configuration: {result['body']}")
    return result["body"]


@router.get("/runs", response_model=dict)
async def list_dograh_runs(
    page: int = 1,
    limit: int = 50,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    s = await _get_setting(db)
    if not s.api_key:
        raise HTTPException(400, "No voice platform API key configured")
    try:
        data = await dograh_client.list_runs(
            s.api_key, s.base_url, page=page, limit=limit,
            start_date=start_date, end_date=end_date,
        )
    except Exception as e:
        raise HTTPException(502, f"Voice platform request failed: {e}")

    runs = []
    for r in data.get("runs", []):
        runs.append(DograhRun(
            run_id=r.get("id"), workflow_id=r.get("workflow_id"),
            workflow_name=r.get("workflow_name"), name=r.get("name"),
            created_at=r.get("created_at", ""),
            duration_seconds=int(r.get("call_duration_seconds") or 0),
            disposition=r.get("disposition"),
            call_type=r.get("call_type"),
            phone_number=r.get("phone_number"),
            charge_usd=r.get("charge_usd"),
            transcript_url=r.get("transcript_url"),
            recording_url=r.get("recording_url"),
            user_recording_url=r.get("user_recording_url"),
            bot_recording_url=r.get("bot_recording_url"),
            transcript_public_url=r.get("transcript_public_url"),
            recording_public_url=r.get("recording_public_url"),
            gathered_context=r.get("gathered_context"),
        ))
    return {
        "runs": [r.model_dump() for r in runs],
        "total_count": data.get("total_count", len(runs)),
        "page": data.get("page", page),
        "limit": data.get("limit", limit),
        "total_pages": data.get("total_pages", 1),
    }


@router.get("/runs/{run_id}/detail", response_model=dict)
async def run_detail(
    run_id: int,
    workflow_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Full run detail from the voice platform (resolves public transcript/recording URLs)."""
    s = await _get_setting(db)
    if not s.api_key:
        raise HTTPException(400, "No voice platform API key configured")
    try:
        return await dograh_client.get_run_detail(s.api_key, s.base_url, workflow_id, run_id)
    except Exception as e:
        raise HTTPException(502, f"Voice platform request failed: {e}")


@router.get("/runs/{run_id}/transcript", response_model=dict)
async def run_transcript(
    run_id: int,
    workflow_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    """Fetch the transcript text for a run (resolves the public URL via run detail)."""
    s = await _get_setting(db)
    if not s.api_key:
        raise HTTPException(400, "No voice platform API key configured")
    transcript_url: Optional[str] = None
    if workflow_id:
        try:
            detail = await dograh_client.get_run_detail(s.api_key, s.base_url, workflow_id, run_id)
            transcript_url = detail.get("transcript_public_url") or detail.get("transcript_url")
        except Exception:
            transcript_url = None
    if not transcript_url:
        runs = await list_dograh_runs(page=1, limit=100, db=db)
        for r in runs["runs"]:
            if r["run_id"] == run_id and r["transcript_public_url"]:
                transcript_url = r["transcript_public_url"]
                break
    if not transcript_url:
        raise HTTPException(404, "No transcript URL found for this run")
    text = await dograh_client.fetch_public_text(transcript_url, s.base_url)
    return {"run_id": run_id, "transcript": text}


@router.get("/file")
async def proxy_dograh_file(url: str, db: AsyncSession = Depends(get_db)):
    """Stream a voice platform recording/transcript file through our backend so the
    browser can play it without touching the voice platform/storage directly."""
    from fastapi.responses import StreamingResponse

    s = await _get_setting(db)
    if not s.api_key:
        raise HTTPException(400, "No voice platform API key configured")
    data, content_type = await dograh_client.fetch_public_bytes(url, s.base_url)
    if not data:
        raise HTTPException(502, "Could not fetch file from voice platform storage")
    return StreamingResponse(iter([data]), media_type=content_type)


@router.get("/runs/by-call-id/{call_uuid}", response_model=dict)
async def find_run_by_call_uuid(call_uuid: str, db: AsyncSession = Depends(get_db)):
    """Find a voice platform run by the call UUID the agent saw
    (gathered_context.call_id / workflow_run_id). Scans recent runs."""
    s = await _get_setting(db)
    if not s.api_key:
        raise HTTPException(400, "No voice platform API key configured")
    try:
        page = 1
        scanned = 0
        while scanned < 500:
            data = await dograh_client.list_runs(s.api_key, s.base_url, page=page, limit=100)
            runs = data.get("runs", [])
            if not runs:
                break
            for r in runs:
                gc = r.get("gathered_context") or {}
                if gc.get("call_id") == call_uuid:
                    return {"found": True, "run_id": r.get("id"), "workflow_id": r.get("workflow_id")}
                if str(r.get("id")) == call_uuid:
                    return {"found": True, "run_id": r.get("id"), "workflow_id": r.get("workflow_id")}
            scanned += len(runs)
            if page >= data.get("total_pages", 1):
                break
            page += 1
    except Exception as e:
        raise HTTPException(502, f"Voice platform request failed: {e}")
    return {"found": False, "run_id": None, "workflow_id": None}


@router.post("/runs/{run_id}/import", response_model=dict)
async def import_run(
    run_id: int,
    workflow_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Import a voice platform run into the local Call Logs + Orders, so analytics and
    the dashboard populate directly from the voice platform (webhook optional)."""
    s = await _get_setting(db)
    if not s.api_key:
        raise HTTPException(400, "No voice platform API key configured")

    try:
        detail = await dograh_client.get_run_detail(s.api_key, s.base_url, workflow_id, run_id)
    except Exception as e:
        raise HTTPException(502, f"Voice platform request failed: {e}")

    call_id = f"voice_{run_id}"
    existing = (await db.execute(select(Call).where(Call.call_id == call_id))).scalar_one_or_none()
    if existing:
        return {"status": "duplicate", "call_id": call_id}

    gc = detail.get("gathered_context") or {}
    transcript_url = detail.get("transcript_public_url") or detail.get("transcript_url") or ""
    transcript_text = ""
    if transcript_url:
        transcript_text = await dograh_client.fetch_public_text(transcript_url, s.base_url)

    turns, tool_calls = _turns_and_tools_from_events(detail)
    transcript = turns or ([{"role": "transcript", "content": transcript_text}] if transcript_text else [])

    logs = detail.get("logs") or {}
    audiohook = logs.get("audiohook") or {}
    genesys_conv = audiohook.get("correlation_id") or audiohook.get("session_id") or ""

    disposition = _map_disposition(gc)
    duration = int(detail.get("duration_seconds") or (detail.get("usage_info") or {}).get("call_duration_seconds") or 0)
    phone = gc.get("customer_phone") or gc.get("phone_number") or ""
    customer_name = gc.get("customer_name") or ""

    summary = _summary_from_gathered(gc, disposition)

    call = Call(
        call_id=call_id,
        disposition=disposition,
        started_at=datetime.now(timezone.utc) - timedelta(seconds=duration),
        ended_at=datetime.now(timezone.utc),
        duration_seconds=duration,
        customer_name=customer_name,
        customer_phone=phone,
        transcript=transcript,
        tool_calls=tool_calls,
        order_numbers=[gc["order_number"]] if gc.get("order_number") else [],
        summary=summary,
        escalation_reason=gc.get("transfer_requested_reason") or "",
        sentiment="",
        cost=detail.get("charge_usd"),
        recording_url=detail.get("recording_public_url") or "",
        transcript_url=transcript_url,
        user_recording_url=detail.get("user_recording_public_url") or "",
        bot_recording_url=detail.get("bot_recording_public_url") or "",
        genesys_conversation_id=genesys_conv,
        workflow_id=workflow_id,
        run_id=run_id,
        workflow_name=detail.get("workflow_name") or "",
        call_type=detail.get("call_type") or "",
        direction=detail.get("call_type") or "inbound",
    )
    db.add(call)

    if phone:
        cust = (await db.execute(select(Customer).where(Customer.phone == phone))).scalar_one_or_none()
        if not cust:
            cust = Customer(phone=phone, name=customer_name)
            db.add(cust)
            await db.flush()
        if customer_name:
            cust.name = cust.name or customer_name
        vec = await _embed(summary)
        db.add(Memory(customer_id=cust.id, kind="call_summary", text=summary, embedding=vec))

    if gc.get("order_number"):
        await _import_order(db, gc, customer_name, phone, call_id)

    await db.commit()
    return {"status": "imported", "call_id": call_id, "disposition": disposition}


def _turns_and_tools_from_events(detail: dict) -> tuple[list[dict], list[dict]]:
    """Build turn-by-turn transcript + tool calls from voice platform realtime events."""
    turns: list[dict] = []
    tools: list[dict] = []
    started: dict[str, dict] = {}
    events = (detail.get("logs") or {}).get("realtime_feedback_events") or []
    for e in events:
        etype = e.get("type", "")
        payload = e.get("payload") or {}
        if etype == "rtf-bot-text" and payload.get("text"):
            turns.append({"role": "assistant", "content": payload["text"]})
        elif etype == "rtf-user-transcription" and payload.get("text") and payload.get("final", True):
            turns.append({"role": "customer", "content": payload["text"]})
        elif etype == "rtf-function-call-start":
            name = payload.get("function_name", "?")
            started[payload.get("tool_call_id", "")] = {
                "name": name, "args_summary": _args_summary(payload.get("arguments"))
            }
        elif etype == "rtf-function-call-end":
            tc_id = payload.get("tool_call_id", "")
            entry = started.pop(tc_id, {"name": payload.get("function_name", "?"), "args_summary": ""})
            result = payload.get("result")
            if isinstance(result, str) and len(result) > 200:
                result = result[:200] + "…"
            entry["result"] = result
            tools.append(entry)
    tools.extend(started.values())  # calls that never reported an end
    return turns, tools


def _args_summary(args: Any) -> str:
    if isinstance(args, dict):
        return ", ".join(f"{k}={v}" for k, v in list(args.items())[:6])
    return str(args or "")[:200]


def _map_disposition(gc: dict) -> str:
    if gc.get("transfer_requested") or gc.get("transfer_requested_reason"):
        return "transferred"
    mapped = " ".join(str(gc.get(k) or "") for k in (
        "mapped_call_disposition", "call_disposition",
        "extracted_call_disposition", "call_outcome",
    )).lower()
    if "transfer" in mapped or "xfer" in mapped or "escalat" in mapped or "agent" in mapped:
        return "transferred"
    if "abandon" in mapped or "hang" in mapped or "no_answer" in mapped:
        return "abandoned"
    if "fail" in mapped or "error" in mapped:
        return "failed"
    if gc.get("call_completed_successfully") is False:
        return "failed"
    return "completed"


def _summary_from_gathered(gc: dict, disposition: str) -> str:
    parts = []
    if gc.get("customer_name"):
        parts.append(f"Customer: {gc['customer_name']}.")
    if gc.get("order_number"):
        parts.append(
            f"Order {gc['order_number']} ({gc.get('fulfillment_type') or 'takeaway'}) "
            f"for ${gc.get('total_price') or 0}."
        )
    if gc.get("transfer_requested_reason"):
        parts.append(f"Transferred: {gc['transfer_requested_reason']}.")
    if gc.get("call_outcome"):
        parts.append(f"Outcome: {gc['call_outcome']}.")
    if not parts:
        parts.append({"transferred": "Transferred to agent.",
                      "failed": "Call failed.",
                      "abandoned": "Customer hung up.",
                      "completed": "Call completed."}[disposition])
    return " ".join(parts)


async def _import_order(db: AsyncSession, gc: dict, customer_name: str, phone: str, call_id: str):
    order_number = gc["order_number"]
    order = (await db.execute(select(Order).where(Order.order_number == order_number))).scalar_one_or_none()
    order_type = "takeaway"
    ft = (gc.get("fulfillment_type") or "").lower()
    if ft in ("dinein", "dine_in", "dine-in"):
        order_type = "dinein"
    total = Decimal(str(gc.get("total_price") or 0))
    if order:
        order.total = total if total else order.total
        order.status = "confirmed"
        return
    order = Order(
        order_number=order_number, customer_name=customer_name, customer_phone=phone,
        email=gc.get("customer_email") or "", order_type=order_type,
        status="confirmed", total=total, source="voice_agent", call_id=call_id,
    )
    db.add(order)
    await db.flush()
    items = gc.get("order_items")
    parsed = items if isinstance(items, list) else []
    for item in parsed:
        db.add(OrderItem(
            order_id=order.id, sku=item.get("sku", ""), name=item.get("name", "Item"),
            quantity=int(item.get("quantity", 1)),
            unit_price=Decimal(str(item.get("price", 0))),
            size=item.get("size", ""), notes=item.get("notes", ""),
        ))
        db.add(OrderEvent(order_id=order.id, status="confirmed", note="Imported from voice platform run"))


async def _embed(text: str):
    from app.services.embeddings import embed_one
    return await embed_one(text)


# ---------- Google LLM (transcript order recovery) ----------

async def _get_google(db: AsyncSession) -> GoogleSetting:
    from app.config import get_settings as _gs
    row = (await db.execute(select(GoogleSetting).where(GoogleSetting.id == 1))).scalar_one_or_none()
    if not row:
        row = GoogleSetting(id=1, api_key="", model=_gs().gemini_model)
        db.add(row)
        await db.flush()
    return row


@google_router.get("", response_model=GoogleSettingResponse)
async def get_google_setting(db: AsyncSession = Depends(get_db)):
    s = await _get_google(db)
    return GoogleSettingResponse(
        model=s.model, api_key_masked=_mask(s.api_key),
        has_key=bool(s.api_key), recovery_enabled=s.recovery_enabled,
        use_audio=s.use_audio,
    )


@google_router.put("", response_model=GoogleSettingResponse)
async def update_google_setting(req: GoogleSettingRequest, db: AsyncSession = Depends(get_db)):
    s = await _get_google(db)
    if req.api_key:
        s.api_key = req.api_key.strip()
    if req.model is not None and req.model.strip():
        s.model = req.model.strip()
    if req.recovery_enabled is not None:
        s.recovery_enabled = req.recovery_enabled
    if req.use_audio is not None:
        s.use_audio = req.use_audio
    await db.commit()
    await db.refresh(s)
    return GoogleSettingResponse(
        model=s.model, api_key_masked=_mask(s.api_key),
        has_key=bool(s.api_key), recovery_enabled=s.recovery_enabled,
        use_audio=s.use_audio,
    )


@google_router.post("/recover/{call_id}", response_model=dict)
async def recover_call_order(call_id: str):
    """Manually trigger transcript recovery for one call (dashboard retry button)."""
    from app.services.order_recovery import recover_order_from_call
    return await recover_order_from_call(call_id)
