"""Voice platform integration: settings + proxied runs/transcripts/recordings.

Allows the dashboard to pull calls directly from a voice platform instance using a
user-managed API key (X-API-Key), and to import a run into the Call Logs +
Orders so the analytics populate even without the post-call webhook.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Call, Customer, DograhSetting, GoogleSetting, LangfuseSetting, Memory, Order, OrderEvent, OrderItem
from app.services import dograh_client

router = APIRouter(prefix="/api/integration/dograh", tags=["integration"])
google_router = APIRouter(prefix="/api/integration/google", tags=["integration"])
langfuse_router = APIRouter(prefix="/api/integration/langfuse", tags=["integration"])

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
    # Telephony channel + provider-side call id surfaced from the run payload
    provider: Optional[str] = None
    provider_call_id: Optional[str] = None
    caller_number: Optional[str] = None
    called_number: Optional[str] = None


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
        tele = _extract_telephony(r)
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
            provider=tele["provider"],
            provider_call_id=tele["provider_call_id"],
            caller_number=tele["caller_number"],
            called_number=tele["called_number"],
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
            if r["run_id"] == run_id and (r["transcript_public_url"] or r["transcript_url"]):
                transcript_url = r["transcript_public_url"] or r["transcript_url"]
                break
    if not transcript_url:
        raise HTTPException(404, "No transcript URL found for this run")
    text = await dograh_client.fetch_public_text(transcript_url, s.base_url)
    return {"run_id": run_id, "transcript": text}


@router.get("/file")
async def proxy_dograh_file(
    url: str,
    download: Optional[str] = None,
    filename: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Stream a voice platform recording/transcript file through our backend so the
    browser can play it without touching the voice platform/storage directly.
    Pass ?download=1 to force a file download (Content-Disposition). Accepts
    ?filename= to control the downloaded file name."""
    from fastapi.responses import StreamingResponse
    from urllib.parse import quote, urlsplit
    import os as _os

    s = await _get_setting(db)
    if not s.api_key:
        raise HTTPException(400, "No voice platform API key configured")
    if not (url or "").strip():
        raise HTTPException(400, "Missing ?url=")
    data, content_type, upstream, hops = await dograh_client.fetch_public_bytes(url.strip(), s.base_url)
    if not data:
        chain = " -> ".join(hops) if hops else "no hops"
        if upstream in (404, 410):
            # Time-limited public links expire — reopening run details mints a fresh one.
            raise HTTPException(
                410,
                "This download link has expired (voice platform returned 404). "
                "Reopen the transaction to refresh the run evidence, then download again. "
                f"[{chain}]",
            )
        detail = f"voice platform returned {upstream}" if upstream else "voice platform unreachable"
        raise HTTPException(502, f"Could not fetch file: {detail} [{chain}]")
    wants_download = str(download or "").lower() in ("1", "true", "yes", "on", "download")
    headers: dict[str, str] = {}
    if wants_download:
        name = (filename or "").strip()
        if not name:
            # derive something sensible from the source URL, else fall back
            # to an extension matching the resolved content type.
            path = urlsplit(url).path.rstrip("/")
            base = _os.path.basename(path)
            if base and "." in base and len(base) <= 96:
                name = base
            else:
                ext = {
                    "audio/wav": ".wav", "audio/x-wav": ".wav",
                    "audio/mpeg": ".mp3", "audio/ogg": ".ogg",
                    "audio/mp4": ".m4a", "text/plain": ".txt",
                }.get((content_type or "").split(";")[0].strip(), "")
                kind = "transcript" if (content_type or "").startswith("text/") else "recording"
                name = f"{kind}{ext}"
        # RFC 5987 encoding so spaces/unicode survive Content-Disposition
        headers["Content-Disposition"] = (
            f"attachment; filename=\"{name.replace(chr(34), '')}\"; "
            f"filename*=UTF-8''{quote(name)}"
        )
    return StreamingResponse(iter([data]), media_type=content_type, headers=headers)


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
    # Genesys AudioHook open request carries session + conversation ids;
    # prefer the true conversation id, fall back to correlation/session.
    genesys_conv = (
        audiohook.get("conversation_id") or audiohook.get("conversationId")
        or audiohook.get("correlation_id") or audiohook.get("session_id") or ""
    )

    tele = _extract_telephony(detail)
    provider_call_id = tele["provider_call_id"]
    # Genesys-flavoured alias mirrors the generic id when provider is Genesys.
    if tele["provider"] == "genesys" and provider_call_id:
        genesys_conv = genesys_conv or provider_call_id

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
        provider=tele["provider"] or "dograh",
        provider_call_id=provider_call_id or "",
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
        await _import_order(db, gc, customer_name, phone, call_id,
                            provider=tele["provider"] or "dograh",
                            provider_call_id=provider_call_id or "")

    await db.commit()
    return {"status": "imported", "call_id": call_id, "disposition": disposition}


TELEPHONY_ID_KEYS = (
    "provider_call_id", "telephony_id", "telephony_call_id", "call_sid",
    "telephony_call_sid", "twilio_sid", "twilio_call_sid", "vonage_uuid",
    "plivo_call_uuid", "telnyx_call_id", "call_uuid", "external_call_id",
    "provider_call_sid", "sip_call_id",
    # Genesys AudioHook ids (conversation id preferred over session)
    "conversation_id", "conversationId",
    "genesys_conversation_id", "genesysConversationId",
    "session_id", "sessionId", "correlation_id", "correlationId",
    "genesys_session_id", "genesysSessionId",
    "genesys_correlation_id", "genesysCorrelationId",
    # Last resort: Dograh often stores the provider-side session UUID under
    # gathered_context.call_id (e.g. Genesys AudioHook session id).
    "call_id",
)


def _extract_telephony(detail: dict) -> dict:
    """Pull provider/channel + provider-side telephony id out of a Dograh run.

    Dograh keeps the PSTN identifiers (Twilio/Vonage/Plivo/Telnyx Call SIDs)
    inside initial_context / gathered_context / logs rather than top-level,
    and reports the channel via mode / call_channel / callChannel.
    Everything is optional — missing values come back as "".
    """
    scopes: list[dict] = []
    _logs: dict = {}
    for key in ("initial_context", "gathered_context", "logs", "usage_info",
                "cost_info", "annotations"):
        v = detail.get(key)
        if isinstance(v, dict):
            scopes.append(v)
            if key == "logs":
                _logs = v
    # Genesys AudioHook session/conversation ids live nested under logs.
    _audiohook = _logs.get("audiohook")
    if isinstance(_audiohook, dict):
        scopes.append(_audiohook)
    scopes.append(detail if isinstance(detail, dict) else {})
    # One extra level: Dograh also nests the ids (audiohook/session blocks)
    # inside initial/gathered contexts — scan those too.
    scopes.extend(
        v for scope in list(scopes)
        if isinstance(scope, dict)
        for v in scope.values() if isinstance(v, dict)
    )

    def _scan(keys: tuple[str, ...]) -> str:
        wanted = {k.lower() for k in keys}
        for scope in scopes:
            if not isinstance(scope, dict):
                continue
            lowered = {str(k).strip().lower(): v for k, v in scope.items()}
            for k in keys:
                for cand in (scope.get(k), lowered.get(k.lower())):
                    if isinstance(cand, str) and cand.strip() and "{{" not in cand:
                        return cand.strip()[:128]
            for lk, v in lowered.items():
                if lk in wanted and isinstance(v, str) and v.strip() and "{{" not in v:
                    return v.strip()[:128]
        return ""

    # Conversation id is the Provider ID — scan it across ALL scopes
    # (incl. logs.audiohook + nested context blocks) before any
    # session/correlation id, so a session id in initial_context never
    # beats the conversation id.
    _CONV_KEYS = ("conversation_id", "conversationId",
                 "genesys_conversation_id", "genesysConversationId")
    _SESS_KEYS = ("session_id", "sessionId", "correlation_id", "correlationId",
                 "genesys_session_id", "genesysSessionId",
                 "genesys_correlation_id", "genesysCorrelationId")
    provider_call_id = (
        _scan(_CONV_KEYS)
        or _scan(tuple(k for k in TELEPHONY_ID_KEYS
                       if k.lower() not in (*[c.lower() for c in _CONV_KEYS],
                                             *[s.lower() for s in _SESS_KEYS],
                                             "call_id")))
        or _scan(_SESS_KEYS)
        or _scan(("call_id",))
    )

    provider = ""
    for scope in scopes:
        for k in ("provider", "telephony_provider", "call_provider"):
            v = scope.get(k)
            if isinstance(v, str) and v.strip():
                provider = v.strip().lower()[:32]
                break
        if provider:
            break
    if not provider:
        # channel hints: telephony | web | chat
        for scope in scopes:
            for k in ("call_channel", "callChannel", "channel", "mode"):
                v = scope.get(k)
                if isinstance(v, str) and v.strip().lower() in (
                        "telephony", "web", "chat", "sip"):
                    provider = "dograh-" + v.strip().lower()
                    break
            if provider:
                break
    if not provider:
        provider = "dograh"

    def _num(scope_key_candidates: tuple[str, ...]) -> str:
        for scope in scopes:
            for k in scope_key_candidates:
                v = scope.get(k)
                if isinstance(v, str) and v.strip() and "{{" not in v:
                    return v.strip()[:32]
        return ""

    return {
        "provider": provider,
        "provider_call_id": provider_call_id,
        "caller_number": _num(("caller_number", "callerNumber", "from_number", "from")),
        "called_number": _num(("called_number", "calledNumber", "to_number", "to")),
    }


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
        "action", "end_action", "endReason", "end_reason",
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


async def _import_order(db: AsyncSession, gc: dict, customer_name: str, phone: str, call_id: str,
                      provider: str = "dograh", provider_call_id: str = ""):
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
        if not order.provider_call_id and provider_call_id:
            order.provider = provider
            order.provider_call_id = provider_call_id
        return
    order = Order(
        order_number=order_number, customer_name=customer_name, customer_phone=phone,
        email=gc.get("customer_email") or "", order_type=order_type,
        status="confirmed", total=total, source="voice_agent", call_id=call_id,
        provider=provider, provider_call_id=provider_call_id,
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


# ---------- Langfuse observability (org-level key trio + per-run deep links) ----------

LANGFUSE_DEFAULT_HOST = "https://cloud.langfuse.com"


class LangfuseSettingRequest(BaseModel):
    public_key: Optional[str] = None
    secret_key: Optional[str] = None
    host: Optional[str] = None
    project_id: Optional[str] = None
    enabled: Optional[bool] = None


class LangfuseSettingResponse(BaseModel):
    host: str
    project_id: str = ""
    public_key_masked: str = ""
    has_keys: bool = False
    enabled: bool = True
    dograh_pushed: Optional[bool] = None
    dograh_detail: Optional[str] = None


async def _get_langfuse(db: AsyncSession) -> LangfuseSetting:
    row = (await db.execute(select(LangfuseSetting).where(LangfuseSetting.id == 1))).scalar_one_or_none()
    if not row:
        row = LangfuseSetting(id=1, public_key="", secret_key="", host=LANGFUSE_DEFAULT_HOST)
        db.add(row)
        await db.flush()
    return row


def _trace_url(host: str, project_id: str, trace_id: str) -> str:
    base = (host or LANGFUSE_DEFAULT_HOST).rstrip("/")
    tid = (trace_id or "").strip()
    if project_id.strip():
        return f"{base}/project/{project_id.strip()}/traces/{tid}"
    return f"{base}/traces/{tid}"


# Explicit key names Dograh (or its variants) may use for the trace link.
_TRACE_URL_KEYS = {
    "langfuse_trace_url", "langfuse_url", "trace_url", "traceurl",
    "traceUrl", "langfuse_link", "langfuseLink", "observability_url",
}
_TRACE_ID_KEYS = {
    "langfuse_trace_id", "langfuse_trace", "trace_id", "traceid",
    "langfusetraceid", "traceId", "langfuseTraceId",
}


def _find_langfuse_trace(detail: dict) -> tuple[str, str]:
    """Deep-scan a Dograh run detail for the Langfuse trace link.

    Dograh opens the Langfuse trace for a run, so the run detail is the
    source of truth — the numeric run id is NOT a valid Langfuse trace id
    (e.g. /traces/1499 404s). Walks the whole payload (any nesting, any
    key spelling) and returns (value, dotted key path). A full trace URL
    wins over a bare trace id.
    """
    best_url: tuple[str, str] = ("", "")
    best_id: tuple[str, str] = ("", "")
    stack: list[tuple[str, Any]] = [("", detail)]
    while stack:
        path, node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                p = f"{path}.{k}" if path else str(k)
                stack.append((p, v))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                stack.append((f"{path}[{i}]", v))
        elif isinstance(node, str) and node.strip():
            s = node.strip()
            if "{{" in s or " " in s or len(s) > 500:
                continue
            leaf = path.split(".")[-1].lower() if path else ""
            if s.startswith("http"):
                if "langfuse" in s and "/traces/" in s:
                    return s, path  # exact Langfuse trace link — done
                if not best_url[0] and (leaf in _TRACE_URL_KEYS or "langfuse" in leaf):
                    best_url = (s, path)
            elif not best_id[0] and (leaf in _TRACE_ID_KEYS or "langfuse" in leaf):
                best_id = (s, path)
    if best_url[0]:
        return best_url
    return best_id


@langfuse_router.get("", response_model=LangfuseSettingResponse)
async def get_langfuse_setting(db: AsyncSession = Depends(get_db)):
    s = await _get_langfuse(db)
    return LangfuseSettingResponse(
        host=s.host or LANGFUSE_DEFAULT_HOST,
        project_id=s.project_id or "",
        public_key_masked=_mask(s.public_key),
        has_keys=bool(s.public_key and s.secret_key),
        enabled=s.enabled,
    )


@langfuse_router.put("", response_model=LangfuseSettingResponse)
async def update_langfuse_setting(req: LangfuseSettingRequest, db: AsyncSession = Depends(get_db)):
    """Save the Langfuse trio locally AND push it to the Dograh org config.

    Local save always wins; the Dograh push is best-effort (reported via
    dograh_pushed/dograh_detail) so a Dograh field-name drift never blocks save.
    """
    s = await _get_langfuse(db)
    if req.public_key:
        s.public_key = req.public_key.strip()
    if req.secret_key:
        s.secret_key = req.secret_key.strip()
    if req.host is not None and req.host.strip():
        s.host = req.host.strip().rstrip("/")
    if req.project_id is not None:
        s.project_id = req.project_id.strip()
    if req.enabled is not None:
        s.enabled = req.enabled
    await db.commit()
    await db.refresh(s)

    pushed: Optional[bool] = None
    detail_msg: Optional[str] = None
    if s.public_key and s.secret_key:
        d = await _get_setting(db)
        if d.api_key:
            try:
                res = await dograh_client.push_langfuse_config(
                    d.api_key, d.base_url, s.public_key, s.secret_key, s.host)
                pushed = bool(res.get("pushed"))
                detail_msg = str(res.get("detail") or "")
            except Exception as e:
                pushed = False
                detail_msg = f"push failed: {e}"
        else:
            pushed = False
            detail_msg = "saved locally; no Dograh API key configured so nothing was pushed"
    return LangfuseSettingResponse(
        host=s.host or LANGFUSE_DEFAULT_HOST,
        project_id=s.project_id or "",
        public_key_masked=_mask(s.public_key),
        has_keys=bool(s.public_key and s.secret_key),
        enabled=s.enabled,
        dograh_pushed=pushed,
        dograh_detail=detail_msg,
    )


@langfuse_router.get("/trace-url", response_model=dict)
async def langfuse_trace_url(
    run_id: Optional[int] = None,
    workflow_id: Optional[int] = None,
    call_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Deep-link one run/call to its Langfuse trace.

    Priority: explicit ?trace_id= → the trace link Dograh itself carries on
    the run detail (same URL Dograh opens) → call_id as a best guess (only
    when no run is linked). The numeric run id is never used — it is not a
    valid Langfuse trace id.
    """
    s = await _get_langfuse(db)
    if not s.enabled:
        raise HTTPException(400, "Langfuse integration is disabled")
    tid = (trace_id or "").strip()
    if tid:
        return {"url": _trace_url(s.host, s.project_id or "", tid),
                "trace_id": tid, "source": "explicit"}
    if run_id and workflow_id:
        d = await _get_setting(db)
        if not d.api_key:
            raise HTTPException(400, "No voice platform API key configured")
        try:
            detail = await dograh_client.get_run_detail(d.api_key, d.base_url, workflow_id, run_id)
        except Exception as e:
            raise HTTPException(502, f"Voice platform request failed: {e}")
        found, matched_key = _find_langfuse_trace(detail if isinstance(detail, dict) else {})
        if found.startswith("http"):
            return {"url": found, "trace_id": found,
                    "source": "dograh", "matched_key": matched_key}
        if found:
            return {"url": _trace_url(s.host, s.project_id or "", found),
                    "trace_id": found, "source": "dograh", "matched_key": matched_key}
        top = sorted(detail.keys())[:30] if isinstance(detail, dict) else []
        raise HTTPException(404,
            "This run carries no Langfuse trace link in its run detail, so there is "
            "nothing valid to open (the run id itself is not a trace id). "
            f"Top-level run keys: {', '.join(top) or 'none'}. "
            "Open the run in Dograh, copy its trace link, and retry with ?trace_id=.")
    if (call_id or "").strip():
        cid = call_id.strip()
        return {"url": _trace_url(s.host, s.project_id or "", cid),
                "trace_id": cid, "source": "call-id"}
    raise HTTPException(400, "Pass ?trace_id=, ?run_id=&workflow_id=, or ?call_id=")


# ---------- Knowledge base (Dograh org documents) ----------

KB_ALLOWED_EXTS = {".pdf", ".docx", ".doc", ".txt", ".json"}
KB_MAX_BYTES = 5 * 1024 * 1024

KB_MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".txt": "text/plain",
    ".json": "application/json",
}


@router.get("/knowledge-base", response_model=dict)
async def list_kb_documents(
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """List Dograh knowledge-base documents for this org (proxied)."""
    s = await _get_setting(db)
    if not s.api_key:
        raise HTTPException(400, "No voice platform API key configured")
    try:
        return await dograh_client.list_kb_documents(
            s.api_key, s.base_url, status=status, limit=limit, offset=offset)
    except Exception as e:
        raise HTTPException(502, f"Voice platform request failed: {e}")


@router.post("/knowledge-base/upload", response_model=dict)
async def upload_kb_document(
    file: UploadFile = File(...),
    retrieval_mode: str = Form(default="chunked"),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file (PDF/DOCX/TXT/JSON, ≤5 MB) to the Dograh knowledge base.

    Flow: presigned URL → PUT bytes → process-document (chunking+embedding).
    Returns the Dograh document record (processing continues in background).
    """
    s = await _get_setting(db)
    if not s.api_key:
        raise HTTPException(400, "No voice platform API key configured")
    import os as _os
    filename = (file.filename or "document").strip() or "document"
    ext = _os.path.splitext(filename)[1].lower()
    if ext not in KB_ALLOWED_EXTS:
        raise HTTPException(
            400, f"Unsupported file type {ext or '(none)'} — use PDF, DOCX, TXT or JSON")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > KB_MAX_BYTES:
        raise HTTPException(400, "File too large — Dograh limit is 5 MB")
    mime = file.content_type or KB_MIME_BY_EXT[ext]
    mode = retrieval_mode if retrieval_mode in ("chunked", "full_document") else "chunked"
    try:
        up = await dograh_client.get_kb_upload_url(s.api_key, s.base_url, filename, mime)
        await dograh_client.put_bytes_to_presigned_url(up["upload_url"], data, mime)
        doc = await dograh_client.process_kb_document(
            s.api_key, s.base_url, up["document_uuid"], up["s3_key"], mode)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Voice platform request failed: {e}")
    return {"status": "uploaded", "retrieval_mode": mode, "document": doc}


@router.delete("/knowledge-base/{document_uuid}", response_model=dict)
async def delete_kb_document(document_uuid: str, db: AsyncSession = Depends(get_db)):
    s = await _get_setting(db)
    if not s.api_key:
        raise HTTPException(400, "No voice platform API key configured")
    try:
        await dograh_client.delete_kb_document(s.api_key, s.base_url, document_uuid)
    except Exception as e:
        raise HTTPException(502, f"Voice platform request failed: {e}")
    return {"status": "deleted", "document_uuid": document_uuid}


@router.get("/knowledge-base/{document_uuid}/download")
async def download_kb_document(document_uuid: str, db: AsyncSession = Depends(get_db)):
    """Download a KB document's original file through our backend.

    The browser hits this URL directly (anchor with `download`), so the file
    lands on whatever PC/machine the browser runs on. Resolution order:
    minted download-url → metadata file/download URL fields → 502 with a
    clear reason when the voice platform exposes no file bytes.
    """
    from fastapi.responses import StreamingResponse
    from urllib.parse import quote

    s = await _get_setting(db)
    if not s.api_key:
        raise HTTPException(400, "No voice platform API key configured")
    uuid = (document_uuid or "").strip()
    if not uuid:
        raise HTTPException(400, "Missing document id")

    meta: dict = {}
    try:
        meta = await dograh_client.get_kb_document(s.api_key, s.base_url, uuid)
        if not isinstance(meta, dict):
            meta = {}
    except Exception:
        meta = {}
    doc = meta.get("document") if isinstance(meta.get("document"), dict) else meta
    filename = str(doc.get("filename") or doc.get("file_name") or f"{uuid}.bin")

    candidates: list[str] = []
    try:
        dl = await dograh_client.get_kb_download_url(s.api_key, s.base_url, uuid)
        if isinstance(dl, dict):
            for k in ("download_url", "downloadUrl", "url", "file_url",
                      "fileUrl", "presigned_url", "presignedUrl"):
                v = dl.get(k)
                if isinstance(v, str) and v.strip():
                    candidates.append(v.strip())
    except Exception:
        pass
    for k in ("download_url", "downloadUrl", "file_url", "fileUrl",
              "source_url", "s3_url", "url"):
        v = doc.get(k)
        if isinstance(v, str) and v.strip():
            candidates.append(v.strip())
    # seen list responses nest the URL one level deeper sometimes
    for k, v in list(doc.items()):
        if isinstance(v, dict):
            for kk in ("download_url", "url", "file_url"):
                vv = v.get(kk)
                if isinstance(vv, str) and vv.strip() and vv.strip() not in candidates:
                    candidates.append(vv.strip())

    for url in candidates:
        data, content_type, upstream, _hops = await dograh_client.fetch_public_bytes(
            url, s.base_url)
        if data:
            disp = (f"attachment; filename=\"{filename.replace(chr(34), '')}\"; "
                    f"filename*=UTF-8''{quote(filename)}")
            return StreamingResponse(iter([data]), media_type=content_type or "application/octet-stream",
                                     headers={"Content-Disposition": disp})
    raise HTTPException(
        502,
        "No downloadable file URL for this document on the voice platform "
        "(tried download-url endpoints + metadata file fields). The file may "
        "be chunked/embedded-only — re-upload the original to keep a copy.")
