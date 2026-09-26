"""Dograh telephony configurations, proxied: providers, configs, phone numbers.

Lets the dashboard manage the same telephony-config screen Dograh has —
connect Twilio/Vonage/Plivo/Telnyx/Vobiz/Exotel/Cloudonix/ARI, attach phone
numbers with inbound workflows, and pick the default outbound caller —
without opening Dograh. Needs the voice-platform API key in Settings.
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.routers.integration import _get_setting
from app.services import dograh_client
from app.services.dograh_client import DograhProxyError

router = APIRouter(prefix="/api/integration/dograh/telephony", tags=["integration"])


class TelephonyConfigCreate(BaseModel):
    name: str
    provider: str
    credentials: dict[str, Any] = {}
    is_default_outbound: bool = False


class TelephonyConfigUpdate(BaseModel):
    name: Optional[str] = None
    credentials: Optional[dict[str, Any]] = None


class PhoneNumberCreate(BaseModel):
    address: str
    label: Optional[str] = None
    country_code: Optional[str] = None
    inbound_workflow_id: Optional[int] = None
    is_active: bool = True
    is_default_caller_id: bool = False


class PhoneNumberUpdate(BaseModel):
    label: Optional[str] = None
    inbound_workflow_id: Optional[int] = None
    clear_inbound_workflow: bool = False
    is_active: Optional[bool] = None
    country_code: Optional[str] = None


async def _creds(db: AsyncSession) -> tuple[str, str]:
    s = await _get_setting(db)
    if not s.api_key:
        raise HTTPException(400, "No voice platform API key configured")
    return s.api_key, s.base_url


def _forward(fn, *args):
    async def _run():
        try:
            return await fn(*args)
        except DograhProxyError as e:
            # Forward Dograh's own 4xx (e.g. credential validation) as-is;
            # only network-level failures become 502.
            raise HTTPException(e.status if e.status < 500 else 502, e.detail)
    return _run()


@router.get("/providers", response_model=dict)
async def providers(db: AsyncSession = Depends(get_db)):
    api_key, base_url = await _creds(db)
    body = await _forward(dograh_client.telephony_providers_metadata, api_key, base_url)
    return body if isinstance(body, dict) else {"providers": body}


@router.get("/warnings", response_model=dict)
async def warnings(db: AsyncSession = Depends(get_db)):
    api_key, base_url = await _creds(db)
    body = await _forward(dograh_client.telephony_config_warnings, api_key, base_url)
    return body if isinstance(body, dict) else {"warnings": body}


@router.get("/configs", response_model=dict)
async def list_configs(db: AsyncSession = Depends(get_db)):
    api_key, base_url = await _creds(db)
    body = await _forward(dograh_client.list_telephony_configs, api_key, base_url)
    if isinstance(body, dict):
        return body
    return {"configurations": body}


@router.post("/configs", response_model=dict)
async def create_config(req: TelephonyConfigCreate, db: AsyncSession = Depends(get_db)):
    api_key, base_url = await _creds(db)
    payload: dict[str, Any] = {
        "name": req.name.strip(),
        "config": {"provider": req.provider.strip().lower(), **(req.credentials or {})},
    }
    if req.is_default_outbound:
        payload["is_default_outbound"] = True
    body = await _forward(dograh_client.create_telephony_config, api_key, base_url, payload)
    return body if isinstance(body, dict) else {"configuration": body}


@router.get("/configs/{config_id}", response_model=dict)
async def get_config(config_id: int, db: AsyncSession = Depends(get_db)):
    api_key, base_url = await _creds(db)
    body = await _forward(dograh_client.get_telephony_config, api_key, base_url, config_id)
    return body if isinstance(body, dict) else {"configuration": body}


@router.put("/configs/{config_id}", response_model=dict)
async def update_config(config_id: int, req: TelephonyConfigUpdate,
                        db: AsyncSession = Depends(get_db)):
    api_key, base_url = await _creds(db)
    payload: dict[str, Any] = {}
    if req.name is not None and req.name.strip():
        payload["name"] = req.name.strip()
    if req.credentials is not None:
        payload["config"] = dict(req.credentials)
    body = await _forward(
        dograh_client.update_telephony_config, api_key, base_url, config_id, payload)
    return body if isinstance(body, dict) else {"configuration": body}


@router.delete("/configs/{config_id}", response_model=dict)
async def delete_config(config_id: int, db: AsyncSession = Depends(get_db)):
    api_key, base_url = await _creds(db)
    await _forward(dograh_client.delete_telephony_config, api_key, base_url, config_id)
    return {"status": "deleted", "config_id": config_id}


@router.post("/configs/{config_id}/set-default-outbound", response_model=dict)
async def make_default_outbound(config_id: int, db: AsyncSession = Depends(get_db)):
    api_key, base_url = await _creds(db)
    body = await _forward(dograh_client.set_default_outbound, api_key, base_url, config_id)
    return body if isinstance(body, dict) else {"status": "ok"}


@router.post("/configs/{config_id}/reactivate", response_model=dict)
async def reactivate(config_id: int, db: AsyncSession = Depends(get_db)):
    api_key, base_url = await _creds(db)
    body = await _forward(dograh_client.reactivate_telephony_config, api_key, base_url, config_id)
    return body if isinstance(body, dict) else {"status": "ok"}


@router.get("/configs/{config_id}/numbers", response_model=dict)
async def list_numbers(config_id: int, db: AsyncSession = Depends(get_db)):
    api_key, base_url = await _creds(db)
    body = await _forward(dograh_client.list_phone_numbers, api_key, base_url, config_id)
    if isinstance(body, dict):
        return body
    return {"phone_numbers": body}


@router.post("/configs/{config_id}/numbers", response_model=dict)
async def add_number(config_id: int, req: PhoneNumberCreate, db: AsyncSession = Depends(get_db)):
    api_key, base_url = await _creds(db)
    payload: dict[str, Any] = {"address": req.address.strip(), "is_active": req.is_active}
    if req.label:
        payload["label"] = req.label.strip()
    if req.country_code:
        payload["country_code"] = req.country_code.strip().upper()[:2]
    if req.inbound_workflow_id:
        payload["inbound_workflow_id"] = req.inbound_workflow_id
    if req.is_default_caller_id:
        payload["is_default_caller_id"] = True
    body = await _forward(dograh_client.add_phone_number, api_key, base_url, config_id, payload)
    return body if isinstance(body, dict) else {"phone_number": body}


@router.put("/configs/{config_id}/numbers/{number_id}", response_model=dict)
async def update_number(config_id: int, number_id: int, req: PhoneNumberUpdate,
                        db: AsyncSession = Depends(get_db)):
    api_key, base_url = await _creds(db)
    payload: dict[str, Any] = {}
    if req.label is not None:
        payload["label"] = req.label
    if req.clear_inbound_workflow:
        payload["clear_inbound_workflow"] = True
    elif req.inbound_workflow_id is not None:
        payload["inbound_workflow_id"] = req.inbound_workflow_id
    if req.is_active is not None:
        payload["is_active"] = req.is_active
    if req.country_code:
        payload["country_code"] = req.country_code.strip().upper()[:2]
    body = await _forward(
        dograh_client.update_phone_number, api_key, base_url, config_id, number_id, payload)
    return body if isinstance(body, dict) else {"phone_number": body}


@router.delete("/configs/{config_id}/numbers/{number_id}", response_model=dict)
async def delete_number(config_id: int, number_id: int, db: AsyncSession = Depends(get_db)):
    api_key, base_url = await _creds(db)
    await _forward(dograh_client.delete_phone_number, api_key, base_url, config_id, number_id)
    return {"status": "deleted", "number_id": number_id}


@router.post("/configs/{config_id}/numbers/{number_id}/set-default-caller", response_model=dict)
async def make_default_caller(config_id: int, number_id: int, db: AsyncSession = Depends(get_db)):
    api_key, base_url = await _creds(db)
    body = await _forward(
        dograh_client.set_default_caller, api_key, base_url, config_id, number_id)
    return body if isinstance(body, dict) else {"status": "ok"}
