"""Proxy client for the Dograh REST API.

The dashboard talks to Dograh with a user-managed API key (X-API-Key header).
Endpoints used:
  - GET /api/v1/organizations/usage/runs        (list runs + public urls)
  - GET /api/v1/workflow/{workflow_id}/runs/{run_id}  (run detail)

The base URL defaults to https://voice.larynxai.in and is overridable in the
integration settings.
"""

from typing import Any, Optional

import httpx
from loguru import logger

from app.config import get_settings

settings = get_settings()

DEFAULT_BASE_URL = "https://voice.larynxai.in"


def _base(base_url: str) -> str:
    return (base_url or DEFAULT_BASE_URL).rstrip("/")


async def dograh_health_check(api_key: str, base_url: str) -> dict:
    """Ping the Dograh health endpoint to verify base URL + key connectivity."""
    url = f"{_base(base_url)}/api/v1/health"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return {"ok": False, "status": r.status_code, "detail": r.text[:200]}
            return {"ok": True, "status": r.status_code, "data": r.json()}
    except Exception as e:
        return {"ok": False, "status": None, "detail": str(e)}


async def list_runs(
    api_key: str,
    base_url: str,
    page: int = 1,
    limit: int = 50,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    filters: Optional[str] = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"page": page, "limit": min(limit, 100)}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    if filters:
        params["filters"] = filters
    url = f"{_base(base_url)}/api/v1/organizations/usage/runs"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, params=params, headers={"X-API-Key": api_key})
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        logger.warning(f"Dograh list_runs failed ({e.response.status_code}): {e.response.text[:200]}")
        raise
    except Exception as e:
        logger.warning(f"Dograh list_runs error: {e}")
        raise


async def get_run_detail(
    api_key: str, base_url: str, workflow_id: int, run_id: int
) -> dict[str, Any]:
    url = f"{_base(base_url)}/api/v1/workflow/{workflow_id}/runs/{run_id}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, headers={"X-API-Key": api_key})
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        logger.warning(f"Dograh get_run_detail failed ({e.response.status_code}): {e.response.text[:200]}")
        raise
    except Exception as e:
        logger.warning(f"Dograh get_run_detail error: {e}")
        raise


async def fetch_public_text(url: str, base_url: str = "") -> str:
    """Fetch a time-limited public transcript URL (text). Resolves the
    storage redirect and rewrites localhost hosts so it works from Docker."""
    data, _ = await fetch_public_bytes(url, base_url)
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _with_host(url: str, base: str) -> str:
    """Replace scheme+host of url with base's (keeps path/query)."""
    from urllib.parse import urlsplit, urlunsplit

    b = urlsplit(base)
    u = urlsplit(url)
    return urlunsplit((b.scheme, b.netloc, u.path, u.query, u.fragment))


def _container_reachable(url: str) -> str:
    """localhost inside a container is itself — point it at the host."""
    from urllib.parse import urlsplit, urlunsplit

    u = urlsplit(url)
    host = u.hostname or ""
    if host in ("localhost", "127.0.0.1"):
        netloc = u.netloc.replace(host, "host.docker.internal", 1)
        return urlunsplit((u.scheme, netloc, u.path, u.query, u.fragment))
    return url


async def fetch_public_bytes(url: str, base_url: str = "") -> tuple[bytes, str]:
    """Resolve a Dograh public/storage URL to raw bytes.

    Public download links 302 to the storage service (often localhost:8080),
    which a container can't reach directly — so localhost hosts are rewritten
    for container→host access at each hop. Non-local hosts (S3, CDN, the
    voice host itself) are left untouched; relative paths are joined to base.
    """
    base = _base(base_url) if base_url else ""
    current = _resolve_start_url(url, base)
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            for _ in range(5):
                r = await client.get(current)
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    current = _container_reachable(r.headers["location"])
                    continue
                if r.status_code == 200:
                    ctype = r.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
                    return r.content, _fix_content_type(ctype, current, r.content)
                logger.warning(f"fetch_public_bytes {r.status_code} for {current[:100]}")
                return b"", ""
    except Exception as e:
        logger.warning(f"fetch_public_bytes error: {e}")
    return b"", ""


def _resolve_start_url(url: str, base: str) -> str:
    """Build the first URL to fetch: keep absolute URLs as-is (except the
    localhost→host rewrite); only join relative paths onto the base host."""
    from urllib.parse import urlsplit, urljoin

    u = (url or "").strip()
    if not u:
        return u
    if base and not urlsplit(u).netloc:
        # relative path like /api/v1/... → join onto the voice host
        return urljoin(base.rstrip("/") + "/", u.lstrip("/"))
    return _container_reachable(u)


def _fix_content_type(ctype: str, url: str, data: bytes) -> str:
    """Storage often serves everything as octet-stream, which browsers refuse
    to play in <audio>. Correct it from extension + magic bytes."""
    if ctype != "application/octet-stream":
        return ctype
    lower = url.lower().split("?")[0]
    if lower.endswith(".wav"):
        return "audio/wav"
    if lower.endswith(".mp3"):
        return "audio/mpeg"
    if lower.endswith(".ogg") or lower.endswith(".oga"):
        return "audio/ogg"
    if lower.endswith(".m4a"):
        return "audio/mp4"
    if lower.endswith(".txt"):
        return "text/plain; charset=utf-8"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    if data[:4] == b"OggS":
        return "audio/ogg"
    return ctype


async def get_model_config(api_key: str, base_url: str) -> dict[str, Any]:
    """GET org-level AI model configuration (masked keys)."""
    url = f"{_base(base_url)}/api/v1/organizations/model-configurations/v2"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, headers={"X-API-Key": api_key})
        r.raise_for_status()
        return r.json()


async def save_model_config(api_key: str, base_url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """PUT org-level AI model configuration (BYOK keys or Dograh-managed)."""
    url = f"{_base(base_url)}/api/v1/organizations/model-configurations/v2"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.put(
            url,
            json=payload,
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        )
        return {"status": r.status_code, "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:1000]}


async def push_langfuse_config(
    api_key: str, base_url: str, public_key: str, secret_key: str, host: str,
) -> dict[str, Any]:
    """Push the Langfuse key trio into the Dograh org config (best-effort).

    Merges into the live org model-config so unrelated keys are preserved.
    Dograh versions differ on the exact field names, so the trio is written
    under the standard `langfuse` block plus common flat aliases.
    Returns {"pushed": bool, "detail": str}.
    """
    try:
        current = await get_model_config(api_key, base_url)
    except Exception as e:
        return {"pushed": False, "detail": f"read current config failed: {e}"}
    cfg = current.get("effective_configuration") or current.get("configuration") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    merged: dict[str, Any] = dict(cfg)
    langfuse_block = dict(merged.get("langfuse") or {})
    langfuse_block.update({
        "public_key": public_key,
        "secret_key": secret_key,
        "host": host or "https://cloud.langfuse.com",
        "enabled": True,
    })
    merged["langfuse"] = langfuse_block
    # flat aliases for Dograh variants that read top-level keys
    merged["langfuse_public_key"] = public_key
    merged["langfuse_secret_key"] = secret_key
    merged["langfuse_host"] = host or "https://cloud.langfuse.com"
    try:
        result = await save_model_config(api_key, base_url, merged)
    except Exception as e:
        return {"pushed": False, "detail": f"write failed: {e}"}
    status = result.get("status", 0)
    if isinstance(status, int) and status < 400:
        return {"pushed": True, "detail": "saved to Dograh org config"}
    return {"pushed": False, "detail": f"Dograh rejected ({status}): {str(result.get('body'))[:200]}"}


# ---------- Knowledge base (Dograh org documents) ----------
# Flow (per Dograh OpenAPI): POST /knowledge-base/upload-url (filename +
# mime_type) -> PUT file bytes to the presigned upload_url -> POST
# /knowledge-base/process-document (document_uuid + s3_key + retrieval_mode).

async def list_kb_documents(
    api_key: str, base_url: str, status: Optional[str] = None,
    limit: int = 100, offset: int = 0,
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": min(max(limit, 1), 100), "offset": max(offset, 0)}
    if status:
        params["status"] = status
    url = f"{_base(base_url)}/api/v1/knowledge-base/documents"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, params=params, headers={"X-API-Key": api_key})
        r.raise_for_status()
        return r.json()


async def get_kb_upload_url(
    api_key: str, base_url: str, filename: str, mime_type: str,
) -> dict[str, Any]:
    url = f"{_base(base_url)}/api/v1/knowledge-base/upload-url"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            url,
            json={"filename": filename, "mime_type": mime_type},
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        )
        r.raise_for_status()
        return r.json()


async def put_bytes_to_presigned_url(
    upload_url: str, data: bytes, content_type: str,
) -> None:
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.put(
            upload_url, content=data,
            headers={"Content-Type": content_type or "application/octet-stream"},
        )
        r.raise_for_status()


async def process_kb_document(
    api_key: str, base_url: str, document_uuid: str, s3_key: str,
    retrieval_mode: str = "chunked",
) -> dict[str, Any]:
    mode = retrieval_mode if retrieval_mode in ("chunked", "full_document") else "chunked"
    url = f"{_base(base_url)}/api/v1/knowledge-base/process-document"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            url,
            json={"document_uuid": document_uuid, "s3_key": s3_key,
                  "retrieval_mode": mode},
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        )
        r.raise_for_status()
        return r.json()


async def delete_kb_document(api_key: str, base_url: str, document_uuid: str) -> dict[str, Any]:
    url = f"{_base(base_url)}/api/v1/knowledge-base/documents/{document_uuid}"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.delete(url, headers={"X-API-Key": api_key})
        r.raise_for_status()
        try:
            return r.json() if r.content else {"status": "deleted"}
        except Exception:
            return {"status": "deleted"}


# ---------- Telephony configurations (providers + phone numbers) ----------

class DograhProxyError(Exception):
    """A proxied Dograh call failed — carries the upstream status + message."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


async def _proxy(
    method: str, path: str, api_key: str, base_url: str,
    json_body: Optional[dict] = None, params: Optional[dict] = None,
) -> Any:
    """Generic JSON proxy to the Dograh REST API (X-API-Key auth).

    Returns the decoded body ({} for empty). Raises DograhProxyError with
    the upstream status code so routers can forward Dograh's own 4xx
    validation messages instead of a generic 502.
    """
    url = f"{_base(base_url)}/api/v1{path}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.request(
                method, url, params=params,
                json=json_body if json_body is not None else None,
                headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            )
    except httpx.HTTPError as e:
        raise DograhProxyError(502, f"Voice platform unreachable: {e}")
    if r.status_code >= 400:
        detail = r.text[:800]
        try:
            body = r.json()
            if isinstance(body, dict) and body.get("detail"):
                d = body["detail"]
                detail = str(d)[:800] if not isinstance(d, list) else "; ".join(
                    str(i.get("msg", i)) for i in d[:5])[:800]
        except Exception:
            pass
        raise DograhProxyError(r.status_code, detail or f"Voice platform error {r.status_code}")
    if not r.content:
        return {}
    try:
        return r.json()
    except Exception:
        return {"raw": r.text[:800]}


async def telephony_providers_metadata(api_key: str, base_url: str) -> Any:
    return await _proxy("GET", "/organizations/telephony-providers/metadata", api_key, base_url)


async def telephony_config_warnings(api_key: str, base_url: str) -> Any:
    return await _proxy("GET", "/organizations/telephony-config-warnings", api_key, base_url)


async def list_telephony_configs(api_key: str, base_url: str) -> Any:
    return await _proxy("GET", "/organizations/telephony-configs", api_key, base_url)


async def create_telephony_config(api_key: str, base_url: str, payload: dict) -> Any:
    return await _proxy("POST", "/organizations/telephony-configs", api_key, base_url, payload)


async def get_telephony_config(api_key: str, base_url: str, config_id: int) -> Any:
    return await _proxy("GET", f"/organizations/telephony-configs/{config_id}", api_key, base_url)


async def update_telephony_config(api_key: str, base_url: str, config_id: int, payload: dict) -> Any:
    return await _proxy("PUT", f"/organizations/telephony-configs/{config_id}", api_key, base_url, payload)


async def delete_telephony_config(api_key: str, base_url: str, config_id: int) -> Any:
    return await _proxy("DELETE", f"/organizations/telephony-configs/{config_id}", api_key, base_url)


async def set_default_outbound(api_key: str, base_url: str, config_id: int) -> Any:
    return await _proxy(
        "POST", f"/organizations/telephony-configs/{config_id}/set-default-outbound",
        api_key, base_url, {})


async def reactivate_telephony_config(api_key: str, base_url: str, config_id: int) -> Any:
    return await _proxy(
        "POST", f"/organizations/telephony-configs/{config_id}/reactivate",
        api_key, base_url, {})


async def list_phone_numbers(api_key: str, base_url: str, config_id: int) -> Any:
    return await _proxy(
        "GET", f"/organizations/telephony-configs/{config_id}/phone-numbers",
        api_key, base_url)


async def add_phone_number(api_key: str, base_url: str, config_id: int, payload: dict) -> Any:
    return await _proxy(
        "POST", f"/organizations/telephony-configs/{config_id}/phone-numbers",
        api_key, base_url, payload)


async def update_phone_number(
    api_key: str, base_url: str, config_id: int, number_id: int, payload: dict,
) -> Any:
    return await _proxy(
        "PUT", f"/organizations/telephony-configs/{config_id}/phone-numbers/{number_id}",
        api_key, base_url, payload)


async def delete_phone_number(api_key: str, base_url: str, config_id: int, number_id: int) -> Any:
    return await _proxy(
        "DELETE", f"/organizations/telephony-configs/{config_id}/phone-numbers/{number_id}",
        api_key, base_url)


async def set_default_caller(api_key: str, base_url: str, config_id: int, number_id: int) -> Any:
    return await _proxy(
        "POST",
        f"/organizations/telephony-configs/{config_id}/phone-numbers/{number_id}/set-default-caller",
        api_key, base_url, {})
