# src/percepxion_mcp/client.py
import logging
from typing import Any

import requests

from .config import API_BASE_URL, DEFAULT_ORGANIZATION_ID, REQUEST_TIMEOUT

logger = logging.getLogger("percepxion_mcp")


class PercepxionSession:
    """Stores user authentication headers for the current MCP process."""

    def __init__(self) -> None:
        self.auth_token: str | None = None
        self.csrf_token: str | None = None

    def is_authenticated(self) -> bool:
        return bool(self.auth_token and self.csrf_token)

    def clear(self) -> None:
        self.auth_token = None
        self.csrf_token = None

    def headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.auth_token:
            headers["x-mystq-token"] = self.auth_token
        if self.csrf_token:
            headers["x-csrf-token"] = self.csrf_token
        return headers


session = PercepxionSession()


def _url(path: str) -> str:
    return f"{API_BASE_URL}{path}"


def _resolve_organization(organization_id: str | None) -> str | None:
    """Return caller-supplied organization_id, or the configured default."""
    if organization_id:
        return organization_id
    if DEFAULT_ORGANIZATION_ID:
        logger.warning(
            "No organization_id supplied by caller, falling back to "
            "PERCEPXION_DEFAULT_ORGANIZATION_ID=%s",
            DEFAULT_ORGANIZATION_ID,
        )
    return DEFAULT_ORGANIZATION_ID


def _resolve_tenant(tenant_id: str | None) -> str | None:
    """Deprecated alias for _resolve_organization(). Kept for backward compatibility."""
    return _resolve_organization(tenant_id)


def _ok(data: Any, status_code: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": True, "data": data}
    if status_code is not None:
        result["status_code"] = status_code
    return result


def _err(message: str, status_code: int | None = None, details: Any = None) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False, "error": message}
    if status_code is not None:
        result["status_code"] = status_code
    if details is not None:
        result["details"] = details
    return result


def _require_login() -> dict[str, Any] | None:
    if session.is_authenticated():
        return None
    return _err("Not authenticated. Run login_with_env first.")


def _extract_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"raw_text": response.text}


def _api_post(
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    form_data: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    require_auth: bool = True,
    content_type_json: bool = True,
) -> dict[str, Any]:
    if require_auth:
        login_err = _require_login()
        if login_err:
            return login_err

    headers = session.headers() if require_auth else {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if not content_type_json:
        headers.pop("Content-Type", None)

    try:
        response = requests.post(
            _url(path),
            headers=headers,
            json=json_body,
            data=form_data,
            files=files,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.error("Request failed for %s: %s", path, exc)
        return _err(f"Request failed for {path}: {exc}")

    payload = _extract_json(response)
    if response.status_code == 401:
        logger.warning("Token expired for %s, session cleared", path)
        session.clear()
        return _err("Unauthorized or token expired. Run login_with_env again.", 401, payload)
    if response.status_code >= 400:
        logger.error("API error %s for %s", response.status_code, path)
        return _err(f"API error for {path}", response.status_code, payload)
    return _ok(payload, response.status_code)
