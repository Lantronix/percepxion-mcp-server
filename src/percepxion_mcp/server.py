import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import requests
from fastmcp import FastMCP

from .config import (
    API_BASE_URL,
    USERNAME,
    PASSWORD,
    REQUEST_TIMEOUT,
    FIRMWARE_DIR,
    DEFAULT_TENANT_ID,
    CREDENTIAL_PROVIDER,
)

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] percepxion_mcp: %(message)s",
)
logger = logging.getLogger("percepxion_mcp")

mcp = FastMCP("Percepxion-Server")


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
        headers = {
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


def _resolve_tenant(tenant_id: str | None) -> str | None:
    """Return caller-supplied tenant_id, or the configured default."""
    return tenant_id or DEFAULT_TENANT_ID


def _ok(data: Any, status_code: int | None = None) -> dict[str, Any]:
    result = {"ok": True, "data": data}
    if status_code is not None:
        result["status_code"] = status_code
    return result


def _err(message: str, status_code: int | None = None, details: Any = None) -> dict[str, Any]:
    result = {"ok": False, "error": message}
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


@mcp.tool()
def login_with_env() -> dict[str, Any]:
    """Authenticate using PERCEPXION_USERNAME and PERCEPXION_PASSWORD from .env."""
    if not USERNAME or not PASSWORD:
        return _err("Missing PERCEPXION_USERNAME or PERCEPXION_PASSWORD in .env.")

    resp = _api_post(
        "/v2/user/login",
        json_body={"username": USERNAME, "password": PASSWORD},
        require_auth=False,
    )
    if not resp["ok"]:
        return resp

    data = resp["data"]
    token = data.get("token")
    csrf = data.get("csrf_token")
    if not token or not csrf:
        return _err("Login succeeded but token/csrf_token missing from response.", resp.get("status_code"), data)

    session.auth_token = token
    session.csrf_token = csrf
    logger.info("Authenticated as %s", USERNAME)
    return _ok({"message": "Authenticated successfully.", "username": USERNAME})


@mcp.tool()
def get_device_list(
    search_query: str = "*",
    limit: int = 25,
    offset: int = 0,
    sort: str = "device_name",
    order: str = "asc",
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Search devices and return matching inventory details."""
    payload: dict[str, Any] = {
        "search_string": search_query,
        "offset": max(0, offset),
        "limit": min(max(1, limit), 1000),
        "sort": sort,
        "order": order,
    }
    if (t := _resolve_tenant(tenant_id)):
        payload["tenant_id"] = t
    return _api_post("/v3/device/search", json_body=payload)


@mcp.tool()
def get_device_details(device_id: str | None = None, serial_num: str | None = None, tenant_id: str | None = None) -> dict[str, Any]:
    """Get full device properties by device_id or serial_num."""
    if not device_id and not serial_num:
        return _err("Provide either device_id or serial_num.")

    payload: dict[str, Any] = {}
    if device_id:
        payload["device_id"] = [device_id]
    if serial_num:
        payload["serial_num"] = [serial_num]
    if (t := _resolve_tenant(tenant_id)):
        payload["tenant_id"] = t

    return _api_post("/v3/device/get", json_body=payload)


@mcp.tool()
def get_devices_by_organization(tenant_id: str, limit: int = 100) -> dict[str, Any]:
    """List devices assigned to a specific tenant."""
    payload = {
        "search_string": "*",
        "offset": 0,
        "limit": min(max(1, limit), 1000),
        "tenant_id": tenant_id,
    }
    return _api_post("/v3/device/search", json_body=payload)


@mcp.tool()
def list_tenants(
    search_query: str = "*",
    limit: int = 100,
    offset: int = 0,
    sort: str = "name",
    order: str = "asc",
) -> dict[str, Any]:
    """
    List tenants (organizations) visible to the authenticated user.

    Use this to discover tenant_id values before calling tools that require one.
    Returns tenant names, IDs, and status.

    Args:
        search_query: Filter by tenant name. Use '*' for all.
        limit: Number of results to return (1-1000).
        offset: Pagination offset.
        sort: Field to sort by (default: 'name').
        order: Sort direction, 'asc' or 'desc'.
    """
    payload: dict[str, Any] = {
        "search_string": search_query,
        "limit": min(max(1, limit), 1000),
        "offset": max(0, offset),
        "sort": sort,
        "order": order,
    }
    return _api_post("/v1/tenant/search", json_body=payload)


@mcp.tool()
def import_and_assign_devices(devices: list[dict[str, Any]], tenant_id: str | None = None) -> dict[str, Any]:
    """
    Assign devices to Percepxion tenant/project.
    Each device item must include: device_id, device_name, serial_num.
    """
    if not devices:
        return _err("devices list cannot be empty.")

    results: list[dict[str, Any]] = []
    for device in devices:
        missing = [k for k in ("device_id", "device_name", "serial_num") if not device.get(k)]
        if missing:
            results.append({
                "ok": False,
                "device": device,
                "error": f"Missing required fields: {', '.join(missing)}",
            })
            continue

        payload: dict[str, Any] = {
            "device_id": device["device_id"],
            "device_name": device["device_name"],
            "serial_num": device["serial_num"],
        }
        if device.get("device_description"):
            payload["device_description"] = device["device_description"]
        if (t := _resolve_tenant(tenant_id)):
            payload["tenant_id"] = t

        resp = _api_post("/v3/device/assign", json_body=payload)
        results.append({"device_id": device["device_id"], **resp})

    return _ok({"results": results})


@mcp.tool()
def unassign_devices(device_ids: list[str], tenant_id: str | None = None) -> dict[str, Any]:
    """Unassign one or more devices from project/tenant."""
    if not device_ids:
        return _err("device_ids list cannot be empty.")
    payload: dict[str, Any] = {"device_id": device_ids}
    if (t := _resolve_tenant(tenant_id)):
        payload["tenant_id"] = t
    return _api_post("/v3/device/unassign", json_body=payload)


@mcp.tool()
def remove_device_from_platform(device_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    """Convenience wrapper for removing one device."""
    return unassign_devices([device_id], tenant_id=tenant_id)


@mcp.tool()
def create_smart_group(
    name: str,
    query: str | None = None,
    device_ids: list[str] | None = None,
    description: str = "",
    temporary: bool = False,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Create a Smart Group for targeting bulk operations (firmware updates, config pushes).

    Provide either query (filter expression) or device_ids (explicit list), not both.
    Set temporary=True for one-off operation targets that should not persist.

    Args:
        name: Display name for the Smart Group.
        query: Filter expression (e.g. 'firmware_ver:9.7.0 AND model:console-server').
        device_ids: Explicit list of Percepxion device IDs to include.
        description: Optional human-readable description.
        temporary: If True, the group is flagged for cleanup after use.
        tenant_id: Scope to a specific tenant.
    """
    if not query and not device_ids:
        return _err("Provide query or device_ids.")

    payload: dict[str, Any] = {
        "name": name,
        "description": description,
        "temporary": temporary,
    }
    if query:
        payload["query_string"] = query
    if device_ids:
        payload["device_id"] = device_ids
    if (t := _resolve_tenant(tenant_id)):
        payload["tenant_id"] = t

    return _api_post("/v3/device/smartgroup/create", json_body=payload)


@mcp.tool()
def send_direct_cli_command(device_id: str, command: str, description: str = "Triggered via MCP", tenant_id: str | None = None) -> dict[str, Any]:
    """
    Send a CLI command to one device via a Percepxion job group.

    The command runs asynchronously. Use search_job_groups to retrieve output.
    Commands are logged to stderr for audit purposes.

    Args:
        device_id: Percepxion device ID (from get_device_list).
        command: CLI command string to execute on the device.
        description: Human-readable label stored in the job group record.
        tenant_id: Tenant/org scope required for tenant-owned devices. Falls back
                   to PERCEPXION_DEFAULT_TENANT_ID env var if not supplied.
    """
    logger.info("CLI command dispatched, device_id=%s command=%r", device_id, command)
    effective_tenant_id = tenant_id or DEFAULT_TENANT_ID
    payload = {
        "name": f"CLI_{device_id[:12]}_{int(time.time())}",
        "description": description,
        "enable": True,
        "type": "command",
        "subtype": "cli",
        "op_code": "execute",
        "operation": command,
        "device_id": [device_id],
    }
    if effective_tenant_id:
        payload["tenant_id"] = effective_tenant_id
    return _api_post("/v1/job/jobgroup/create", json_body=payload)


@mcp.tool()
def update_device_config(
    device_id: str,
    items: list[dict[str, str]] | None = None,
    property_name: str | None = None,
    new_value: str | None = None,
    apply_now: bool = True,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Save config changes then optionally create a config pull job to apply them.
    Use either `items` or (`property_name` + `new_value`).
    """
    if not items:
        if not property_name or new_value is None:
            return _err("Provide items or property_name + new_value.")
        items = [{"name": property_name, "value": new_value}]

    save_payload: dict[str, Any] = {
        "device_id": [device_id],
        "items": items,
    }
    if (t := _resolve_tenant(tenant_id)):
        save_payload["tenant_id"] = t

    save_resp = _api_post("/v1/telemetry/config/save", json_body=save_payload)
    if not save_resp["ok"] or not apply_now:
        return save_resp

    job_payload: dict[str, Any] = {
        "name": f"Config_Update_{device_id[:12]}_{int(time.time())}",
        "description": "Apply saved config from MCP",
        "type": "command",
        "subtype": "config",
        "op_code": "execute",
        "operation": "pull",
        "device_id": [device_id],
        "enable": True,
    }
    if (t := _resolve_tenant(tenant_id)):
        job_payload["tenant_id"] = t

    job_resp = _api_post("/v1/job/jobgroup/create", json_body=job_payload)
    return _ok({"save": save_resp["data"], "apply_job": job_resp})


def _resolve_template_id(template_name: str, source_device_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "search_string": template_name,
        "device_id": [source_device_id],
        "offset": 0,
        "limit": 20,
        "sort": "name",
        "order": "desc",
    }
    if (t := _resolve_tenant(tenant_id)):
        payload["tenant_id"] = t

    resp = _api_post("/v1/telemetry/template/search", json_body=payload)
    if not resp["ok"]:
        return resp

    templates = resp["data"].get("template", [])
    for item in templates:
        if item.get("name") == template_name:
            template_id = item.get("id")
            if template_id:
                return _ok({"template_id": template_id})
    return _err("Template created but template_id could not be resolved from template/search.", details=resp["data"])


@mcp.tool()
def clone_device_config(
    source_device_id: str,
    target_device_id: str,
    record_names: list[str],
    template_name: str = "Cloned_Template",
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Create a config template from source and apply it to target device."""
    if not record_names:
        return _err("record_names cannot be empty.")

    template_payload: dict[str, Any] = {
        "name": template_name,
        "description": f"Cloned from {source_device_id}",
        "device_id": source_device_id,
        "selected_config_group": record_names,
    }
    if (t := _resolve_tenant(tenant_id)):
        template_payload["tenant_id"] = t

    create_resp = _api_post("/v1/telemetry/template/create", json_body=template_payload)
    if not create_resp["ok"]:
        return create_resp

    template_id_resp = _resolve_template_id(template_name, source_device_id, tenant_id=tenant_id)
    if not template_id_resp["ok"]:
        return _ok({"template_create": create_resp["data"], "warning": template_id_resp})

    template_id = template_id_resp["data"]["template_id"]
    job_payload: dict[str, Any] = {
        "name": f"Apply_{template_name}",
        "description": f"Apply template {template_name} to target device",
        "type": "command",
        "subtype": "config",
        "op_code": "execute",
        "operation": "pull",
        "config_tml_id": template_id,
        "device_id": [target_device_id],
        "enable": True,
    }
    if (t := _resolve_tenant(tenant_id)):
        job_payload["tenant_id"] = t

    apply_resp = _api_post("/v1/job/jobgroup/create", json_body=job_payload)
    return _ok(
        {
            "template_create": create_resp["data"],
            "template_id": template_id,
            "apply_job": apply_resp,
        }
    )


@mcp.tool()
def get_device_firmware_status(device_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    """Get device details and summarize firmware version/state."""
    resp = get_device_details(device_id=device_id, tenant_id=tenant_id)
    if not resp["ok"]:
        return resp

    data = resp["data"]
    candidates = data.get("results") or data.get("search_results") or []
    if not candidates:
        return _err("Device not found.", details=data)

    device = candidates[0]
    attributes = device.get("attributes", {})
    summary = {
        "device_id": device.get("device_id", device_id),
        "device_name": device.get("device_name"),
        "firmware_ver": attributes.get("firmware_ver"),
        "firmware_updated": attributes.get("firmware_updated"),
        "device_state": attributes.get("device_state"),
    }
    return _ok(summary)


@mcp.tool()
def request_device_syslog_upload(
    device_ids: list[str],
    log_type: str = "all",
    log_level: str = "info",
    from_date: str | None = None,
    to_date: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Trigger device syslog upload jobs.
    Date format must be RFC3339 when provided.
    """
    if not device_ids:
        return _err("device_ids list cannot be empty.")

    log_request: dict[str, Any] = {
        "log_type": log_type,
        "log_level": log_level,
    }
    if from_date:
        log_request["from_date"] = from_date
    if to_date:
        log_request["to_date"] = to_date

    payload: dict[str, Any] = {
        "name": f"Syslog_{int(time.time())}",
        "description": "Request device syslog upload",
        "operation": "upload",
        "type": "command",
        "subtype": "log",
        "op_code": "execute",
        "device_id": device_ids,
        "enable": True,
        "log_request": log_request,
    }
    if (t := _resolve_tenant(tenant_id)):
        payload["tenant_id"] = t
    return _api_post("/v1/job/jobgroup/create", json_body=payload)


@mcp.tool()
def get_device_syslogs(device_id: str, limit: int = 10) -> dict[str, Any]:
    """Query device syslog files already uploaded to Percepxion."""
    payload = {
        "device_id": [device_id],
        "type": "syslog",
        "limit": max(1, limit),
    }
    return _api_post("/v1/storage/file/content/query", json_body=payload)


@mcp.tool()
def get_security_telemetry(device_id: str, selected: bool = True, tenant_id: str | None = None) -> dict[str, Any]:
    """Retrieve telemetry statistics useful for security analysis."""
    payload: dict[str, Any] = {"device_id": device_id, "selected": selected}
    if (t := _resolve_tenant(tenant_id)):
        payload["tenant_id"] = t
    return _api_post("/v1/telemetry/stat/view", json_body=payload)


@mcp.tool()
def investigate_audit_logs(
    search_string: str = "",
    from_date: str | None = None,
    to_date: str | None = None,
    usernames: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
    sort: str = "timestamp",
    order: str = "desc",
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Search detailed audit records.
    If date range is omitted, defaults to the broad range 1970-01-01 through 2100-01-01.
    """
    payload: dict[str, Any] = {
        "from_date": from_date or "1970-01-01T00:00:00Z",
        "to_date": to_date or "2100-01-01T00:00:00Z",
        "search_string": search_string,
        "offset": max(0, offset),
        "limit": min(max(1, limit), 1000),
        "sort": sort,
        "order": order,
    }
    if usernames:
        payload["username"] = usernames
    if (t := _resolve_tenant(tenant_id)):
        payload["tenant_id"] = t
    return _api_post("/v1/audit/search", json_body=payload)


@mcp.tool()
def investigate_user_audit_logs(
    user_filter: str = "",
    limit: int = 50,
    offset: int = 0,
    sort: str = "username",
    order: str = "asc",
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Search user records with last audit action summary."""
    payload: dict[str, Any] = {
        "search_string": user_filter,
        "offset": max(0, offset),
        "limit": min(max(1, limit), 1000),
        "sort": sort,
        "order": order,
    }
    if (t := _resolve_tenant(tenant_id)):
        payload["tenant_id"] = t
    return _api_post("/v1/audit/user/search", json_body=payload)


@mcp.tool()
def update_firmware_by_smart_group(
    firmware_file_path: str,
    smart_group_ids: list[str],
    content_name: str,
    version: str,
    description: str = "Firmware update via MCP",
    enable: bool = True,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Upload firmware and target one or more Smart Groups.
    This maps to POST /v3/content/create multipart/form-data.
    """
    if not smart_group_ids:
        return _err("smart_group_ids cannot be empty.")

    firmware_path = Path(firmware_file_path).resolve()
    if not firmware_path.exists():
        return _err(f"Firmware file not found: {firmware_file_path}")
    if not firmware_path.is_file():
        return _err(f"Firmware path is not a file: {firmware_file_path}")
    if FIRMWARE_DIR:
        allowed = Path(FIRMWARE_DIR).resolve()
        if not firmware_path.is_relative_to(allowed):
            return _err(
                f"Firmware file is outside the allowed directory ({FIRMWARE_DIR}). "
                "Set PERCEPXION_FIRMWARE_DIR to the directory containing your firmware files."
            )

    data_payload: dict[str, Any] = {
        "name": content_name,
        "description": description,
        "version": version,
        "opcode": "download",
        "type": "firmware",
        "enable": enable,
        "smart_group_id": smart_group_ids,
    }
    if (t := _resolve_tenant(tenant_id)):
        data_payload["tenant_id"] = t

    try:
        with firmware_path.open("rb") as firmware_file:
            files = {"file": (firmware_path.name, firmware_file, "application/octet-stream")}
            form_data = {"data": json.dumps(data_payload)}
            return _api_post(
                "/v3/content/create",
                form_data=form_data,
                files=files,
                content_type_json=False,
            )
    except OSError as exc:
        return _err(f"Unable to open firmware file: {exc}")


@mcp.tool()
def search_job_groups(
    search_string: str = "",
    job_type: str = "command",
    subtype: str | None = None,
    limit: int = 50,
    offset: int = 0,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Search job groups to monitor asynchronous operation progress."""
    effective_tenant_id = tenant_id or DEFAULT_TENANT_ID
    payload: dict[str, Any] = {
        "search_string": search_string,
        "type": job_type,
        "offset": max(0, offset),
        "limit": min(max(1, limit), 1000),
    }
    if subtype:
        payload["subtype"] = subtype
    if effective_tenant_id:
        payload["tenant_id"] = effective_tenant_id
    return _api_post("/v1/job/jobgroup/search", json_body=payload)


@mcp.tool()
def query_device_access_log(
    device_id: str,
    log_level: str = "info",
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    """Query device access log entries with pagination."""
    payload = {
        "device_id": device_id,
        "log_level": log_level,
        "offset": max(0, offset),
        "limit": min(max(1, limit), 1000),
    }
    return _api_post("/v1/storage/file/devicelog/query-by-id", json_body=payload)


@mcp.tool()
def download_device_access_log(device_id: str, log_level: str = "info") -> dict[str, Any]:
    """Download complete device access log content."""
    payload = {
        "device_id": device_id,
        "log_level": log_level,
    }
    return _api_post("/v1/storage/file/devicelog/download", json_body=payload)


@mcp.tool()
def firmware_compliance_report(
    expected_firmware_version: str,
    search_query: str = "*",
    tenant_id: str | None = None,
    limit: int = 1000,
    model_filter: str | None = None,
) -> dict[str, Any]:
    """
    Compare fleet firmware versions against an expected version and report compliance.
    """
    inventory = get_device_list(
        search_query=search_query,
        limit=limit,
        offset=0,
        sort="device_name",
        order="asc",
        tenant_id=tenant_id,
    )
    if not inventory.get("ok"):
        return inventory

    data = inventory.get("data", {})
    devices = data.get("search_results", [])

    compliant: list[dict[str, Any]] = []
    non_compliant: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []

    for device in devices:
        attrs = device.get("attributes", {}) or {}
        model = attrs.get("model")
        if model_filter and model != model_filter:
            continue

        actual = attrs.get("firmware_ver")
        item = {
            "device_id": device.get("device_id"),
            "device_name": device.get("device_name"),
            "serial_num": device.get("serial_num"),
            "model": model,
            "firmware_ver": actual,
            "status": device.get("status"),
            "last_contacted": device.get("last_contacted"),
        }

        if not actual:
            unknown.append(item)
        elif actual == expected_firmware_version:
            compliant.append(item)
        else:
            non_compliant.append(item)

    evaluated_total = len(compliant) + len(non_compliant) + len(unknown)
    compliance_pct = round((len(compliant) / evaluated_total) * 100, 2) if evaluated_total else 0.0

    return _ok(
        {
            "expected_firmware_version": expected_firmware_version,
            "searched_total": len(devices),
            "evaluated_total": evaluated_total,
            "compliance_percent": compliance_pct,
            "compliant_count": len(compliant),
            "non_compliant_count": len(non_compliant),
            "unknown_count": len(unknown),
            "non_compliant_devices": non_compliant,
            "unknown_firmware_devices": unknown,
        }
    )


def main() -> None:
    """Run the FastMCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
