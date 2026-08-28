import json
import logging
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from .config import (
    FIRMWARE_DIR,
)
from .client import (
    session,
    _api_post,
    _api_put,
    _ok,
    _err,
    _resolve_organization,
    _harvest_organization_candidates,
    OrganizationResolutionError,
)
from .cli_policy import check_command, CLIPolicyViolation

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] percepxion_mcp: %(message)s",
)
logger = logging.getLogger("percepxion_mcp")

mcp = FastMCP("Percepxion-Server")


def _effective_org_id(organization_id: str | None, tenant_id: str | None) -> str | None:
    """
    Merge the current organization_id param with the legacy tenant_id param.

    Percepxion's product hierarchy is Project > Portal > Organization; "tenant_id"
    predates that terminology and is kept only for backward compatibility.
    organization_id wins if both are supplied. Logs a low-severity deprecation
    notice when only the legacy tenant_id was used.
    """
    if organization_id:
        return organization_id
    if tenant_id:
        logger.debug(
            "tenant_id parameter is deprecated, use organization_id instead (tenant_id=%r)",
            tenant_id,
        )
        return tenant_id
    return None


@mcp.tool()
def login_with_env() -> dict[str, Any]:
    """
    Authenticate using credentials from the configured provider.

    The provider is selected by PERCEPXION_CREDENTIAL_PROVIDER (default: 'env').
    - env: reads PERCEPXION_USERNAME + PERCEPXION_PASSWORD from environment
    - vault: reads from HashiCorp Vault (requires VAULT_ADDR, VAULT_TOKEN, VAULT_SECRET_PATH)
    - aws: reads from AWS Secrets Manager (requires AWS_SECRET_NAME, AWS_REGION)
    - cyberark: reads from CyberArk CCP (requires CYBERARK_URL, CYBERARK_APP_ID, CYBERARK_SAFE, CYBERARK_OBJECT)
    """
    import os
    from .providers import get_provider

    credential_provider = os.getenv("PERCEPXION_CREDENTIAL_PROVIDER", "env")
    try:
        provider = get_provider(credential_provider)
        creds = provider.get_credentials()
    except Exception as exc:
        return _err(f"Failed to load credentials from provider '{credential_provider}': {exc}")

    resp = _api_post(
        "/v2/user/login",
        json_body={"username": creds["username"], "password": creds["password"]},
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

    # Capture the RBAC-authoritative list of organizations this user has
    # permission for. Percepxion's hierarchy is Project > Portal >
    # Organization(tenant), and role determines how that access is expressed
    # in the login response:
    #   - tenant_user: fine-grained, one group entry per permitted org
    #     (user.group[].tenant_id).
    #   - tenant_admin: full access to their own single org
    #     (user.tenant_id), group[] may still be empty.
    #   - project_admin: full access to every org under user.project_id, but
    #     there is no endpoint that enumerates a project's member orgs, and
    #     group[]/tenant_id are both empty for this role by design. Trust
    #     /v3/device/search instead (see trust_harvested_organizations),
    #     which the backend already scopes to what this session can see.
    # Degrade gracefully (empty set, trust off) if fields are missing/
    # malformed rather than raising.
    permitted_ids: set[str] = set()
    user = data.get("user") or {}
    groups = user.get("group") or []
    for group in groups:
        if isinstance(group, dict):
            tenant_id = group.get("tenant_id")
            if tenant_id:
                permitted_ids.add(tenant_id)
    if user.get("tenant_id"):
        permitted_ids.add(user["tenant_id"])
    session.permitted_organization_ids = permitted_ids
    is_project_admin = user.get("role") == "project_admin"
    session.trust_harvested_organizations = is_project_admin
    # Percepxion's API requires an explicit tenant_id on job/telemetry/content/
    # smartgroup/audit calls when the session is project_admin (documented for
    # job/jobgroup/create, empirically confirmed for job/jobgroup/create,
    # job/jobgroup/search, telemetry/result/search); tenant_user/tenant_admin
    # sessions are auto-scoped to their one org and never need it. See
    # _resolve_organization's required= parameter.
    session.requires_explicit_organization = is_project_admin
    logger.debug(
        "Captured %d permitted_organization_ids (role=%s, trust_harvested=%s, requires_explicit_organization=%s)",
        len(permitted_ids), user.get("role"), session.trust_harvested_organizations,
        session.requires_explicit_organization,
    )

    session.correlation_id = uuid.uuid4().hex[:16]
    logger.info(
        "Authenticated via %s provider (session correlation id: %s)",
        credential_provider, session.correlation_id,
    )
    return _ok({
        "message": "Authenticated successfully.",
        "username": creds["username"],
        "correlation_id": session.correlation_id,
    })


def _audit_description(description: str) -> str:
    """Append the per-login correlation id to an audit description so an
    MCP-brokered device action is traceable to one session in both the MCP
    logs and the Percepxion audit trail. No-op if not yet authenticated."""
    if session.correlation_id:
        return f"{description} [mcp-session:{session.correlation_id}]"
    return description


@mcp.tool()
def reconfigure_credentials(provider: str) -> dict[str, Any]:
    """
    Switch the active credential provider and clear the current session.

    After calling this, run login_with_env to authenticate with the new provider.
    Valid providers: 'env', 'vault', 'aws', 'cyberark'.

    Args:
        provider: The credential provider to activate.
    """
    import os
    from .providers import get_provider

    valid = {"env", "vault", "aws", "cyberark"}
    if provider not in valid:
        return _err(f"Invalid provider '{provider}'. Choose: {', '.join(sorted(valid))}")

    try:
        get_provider(provider)  # validate it can be instantiated
    except Exception as exc:
        return _err(f"Provider '{provider}' failed to initialize: {exc}")

    os.environ["PERCEPXION_CREDENTIAL_PROVIDER"] = provider
    session.clear()
    logger.info("Credential provider switched to '%s', session cleared", provider)
    return _ok({"message": f"Provider set to '{provider}'. Run login_with_env to authenticate."})


@mcp.tool()
def get_device_list(
    search_query: str = "*",
    limit: int = 25,
    offset: int = 0,
    sort: str = "device_name",
    order: str = "asc",
    organization_id: str | None = None,
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
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id))):
        payload["tenant_id"] = t
    return _api_post("/v3/device/search", json_body=payload)


@mcp.tool()
def get_device_details(device_id: str | None = None, serial_num: str | None = None, organization_id: str | None = None, tenant_id: str | None = None) -> dict[str, Any]:
    """Get full device properties by device_id or serial_num."""
    if not device_id and not serial_num:
        return _err("Provide either device_id or serial_num.")

    payload: dict[str, Any] = {}
    if device_id:
        payload["device_id"] = [device_id]
    if serial_num:
        payload["serial_num"] = [serial_num]
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id))):
        payload["tenant_id"] = t

    return _api_post("/v3/device/get", json_body=payload)


@mcp.tool()
def get_devices_by_organization(
    organization_id: str | None = None,
    tenant_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """
    List devices assigned to a specific organization.

    Args:
        organization_id: The organization to list devices for. Accepts either a UUID or an exact (case-insensitive) organization name.
        tenant_id: Deprecated alias for organization_id, kept for backward compatibility.
        limit: Number of results to return (1-1000).
    """
    effective = _effective_org_id(organization_id, tenant_id)
    if not effective:
        return _err("Provide organization_id (or the deprecated tenant_id).")
    resolved = _resolve_organization(effective, required=True)
    payload = {
        "search_string": "*",
        "offset": 0,
        "limit": min(max(1, limit), 1000),
        "tenant_id": resolved,
    }
    return _api_post("/v3/device/search", json_body=payload)


def _list_organizations_impl(
    search_query: str = "*",
    limit: int = 100,
    offset: int = 0,
    sort: str = "name",
    order: str = "asc",
) -> dict[str, Any]:
    """
    List organizations the authenticated user has permission for.

    NOTE: Percepxion has no working organization/tenant listing endpoint.
    /v1/tenant/search is defined in the API spec but not implemented
    server-side (it returns a 400 VALIDATION_FAILED in production); the
    previous implementation of this tool called that dead endpoint and
    always failed. This implementation instead treats
    session.permitted_organization_ids (captured from the login response's
    user.group[].tenant_id / user.tenant_id, i.e. this user's RBAC-
    authoritative organization list) as the source of truth for IDs, and
    best-effort resolves display names by scanning visible devices' embedded
    tenant info via /v3/device/search. An organization with zero visible
    devices will still appear (its ID is permission-derived, not
    device-derived) but with name=None, since there is no other way to learn
    its display name.

    For project_admin sessions, group[]/tenant_id are empty by design (that
    role's access is granted at the project level, which Percepxion has no
    endpoint to enumerate directly), so IDs are instead taken from whatever
    /v3/device/search actually returns, that endpoint is already
    backend-scoped to what the authenticated session can see.
    """
    if not session.is_authenticated():
        return _err("Not authenticated. Run login_with_env first.")

    permitted = session.permitted_organization_ids
    trust_harvested = session.trust_harvested_organizations
    if not permitted and not trust_harvested:
        return _ok({
            "organizations": [],
            "total": 0,
            "warning": (
                "No permitted organizations found in the current session. This can happen "
                "if the login response did not include a user.group list, or the "
                "authenticated user genuinely has no organization permissions. "
                "Run login_with_env again if this is unexpected."
            ),
        })

    try:
        candidates = _harvest_organization_candidates()
    except OrganizationResolutionError as exc:
        candidates = {}
        logger.warning("Could not resolve organization display names: %s", exc)

    org_ids = set(permitted)
    if trust_harvested:
        org_ids |= candidates.keys()

    organizations = [
        {"organization_id": org_id, "name": candidates.get(org_id)}
        for org_id in org_ids
    ]

    if search_query and search_query != "*":
        needle = search_query.strip().lower()
        organizations = [
            o for o in organizations if o["name"] and needle in o["name"].lower()
        ]

    reverse = order == "desc"
    if sort == "name":
        organizations.sort(key=lambda o: (o["name"] or "").lower(), reverse=reverse)
    else:
        organizations.sort(key=lambda o: o["organization_id"], reverse=reverse)

    total = len(organizations)
    offset = max(0, offset)
    limit = min(max(1, limit), 1000)
    page = organizations[offset: offset + limit]

    return _ok({"organizations": page, "total": total})


@mcp.tool()
def list_organizations(
    search_query: str = "*",
    limit: int = 100,
    offset: int = 0,
    sort: str = "name",
    order: str = "asc",
) -> dict[str, Any]:
    """
    List organizations the authenticated user has permission for.

    Use this to discover organization_id values before calling tools that require one.
    IDs come from your login session's RBAC permissions (authoritative). Display names
    are best-effort: only resolvable for organizations with at least one visible device,
    an organization with zero visible devices will appear with name=None.

    Args:
        search_query: Case-insensitive substring filter on organization name. Use '*' for all.
        limit: Number of results to return (1-1000).
        offset: Pagination offset.
        sort: Field to sort by, 'name' (default) or 'organization_id'.
        order: Sort direction, 'asc' or 'desc'.
    """
    return _list_organizations_impl(search_query, limit, offset, sort, order)


@mcp.tool()
def list_tenants(
    search_query: str = "*",
    limit: int = 100,
    offset: int = 0,
    sort: str = "name",
    order: str = "asc",
) -> dict[str, Any]:
    """
    Deprecated alias for list_organizations(), kept for backward compatibility.

    Args:
        search_query: Case-insensitive substring filter on organization name. Use '*' for all.
        limit: Number of results to return (1-1000).
        offset: Pagination offset.
        sort: Field to sort by, 'name' (default) or 'organization_id'.
        order: Sort direction, 'asc' or 'desc'.
    """
    return _list_organizations_impl(search_query, limit, offset, sort, order)


@mcp.tool()
def import_and_assign_devices(devices: list[dict[str, Any]], organization_id: str | None = None, tenant_id: str | None = None) -> dict[str, Any]:
    """
    Assign devices to a Percepxion organization/project.
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
        if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
            payload["tenant_id"] = t

        logger.info("Device assign requested, device_id=%s device_name=%s", device["device_id"], device["device_name"])
        resp = _api_post("/v3/device/assign", json_body=payload)
        results.append({"device_id": device["device_id"], **resp})

    return _ok({"results": results})


@mcp.tool()
def unassign_devices(device_ids: list[str], organization_id: str | None = None, tenant_id: str | None = None) -> dict[str, Any]:
    """Unassign one or more devices from a project/organization."""
    if not device_ids:
        return _err("device_ids list cannot be empty.")
    payload: dict[str, Any] = {"device_id": device_ids}
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
        payload["tenant_id"] = t
    logger.info("Device unassign requested, device_ids=%s", device_ids)
    return _api_post("/v3/device/unassign", json_body=payload)


@mcp.tool()
def remove_device_from_platform(device_id: str, organization_id: str | None = None, tenant_id: str | None = None) -> dict[str, Any]:
    """Convenience wrapper for removing one device."""
    logger.info("Device removal from platform requested, device_id=%s", device_id)
    return unassign_devices([device_id], organization_id=organization_id, tenant_id=tenant_id)


@mcp.tool()
def create_smart_group(
    name: str,
    query: str | None = None,
    device_ids: list[str] | None = None,
    description: str = "",
    temporary: bool = False,
    organization_id: str | None = None,
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
        organization_id: Scope to a specific organization. Accepts either a UUID or an exact (case-insensitive) organization name.
        tenant_id: Deprecated alias for organization_id, kept for backward compatibility.
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
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
        payload["tenant_id"] = t

    return _api_post("/v3/device/smartgroup/create", json_body=payload)


@mcp.tool()
def list_smart_groups(
    search_query: str = "*",
    limit: int = 50,
    offset: int = 0,
    organization_id: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    List Smart Groups visible to the authenticated user.

    Use this to find existing group IDs before targeting firmware updates or config pushes.

    Args:
        search_query: Filter by name. Use '*' for all.
        limit: Number of results (1-1000).
        offset: Pagination offset.
        organization_id: Scope to a specific organization. Accepts either a UUID or an exact (case-insensitive) organization name.
        tenant_id: Deprecated alias for organization_id, kept for backward compatibility.
    """
    payload: dict[str, Any] = {
        "search_string": search_query,
        "offset": max(0, offset),
        "limit": min(max(1, limit), 1000),
    }
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
        payload["tenant_id"] = t
    return _api_post("/v3/device/smartgroup/search", json_body=payload)


@mcp.tool()
def delete_smart_group(smart_group_id: str, organization_id: str | None = None, tenant_id: str | None = None) -> dict[str, Any]:
    """
    Delete a Smart Group by ID.

    Use this to clean up temporary groups created for one-off operations.

    Args:
        smart_group_id: The ID of the Smart Group to delete.
        organization_id: Scope to a specific organization. Accepts either a UUID or an exact (case-insensitive) organization name.
        tenant_id: Deprecated alias for organization_id, kept for backward compatibility.
    """
    payload: dict[str, Any] = {"id": smart_group_id}
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
        payload["tenant_id"] = t
    logger.info("Smart Group delete requested, smart_group_id=%s", smart_group_id)
    return _api_post("/v3/device/smartgroup/delete", json_body=payload)


@mcp.tool()
def send_direct_cli_command(
    device_id: str,
    command: str,
    description: str = "Triggered via MCP",
    organization_id: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Send a CLI command to one device via a Percepxion job group.

    Commands run asynchronously. Poll search_job_groups or get_job_group for
    job status (job_status_clone.state reaches "Completed"/"Failed"), then
    call get_cli_command_output with this call's job_group_id to retrieve the
    actual device output text. Commands are logged to stderr for audit purposes.

    Policy (configured via env vars):
    - Read-only by default (PERCEPXION_CLI_WRITE_ENABLED=false).
      Only 'show', 'get', 'ping', 'traceroute', and similar read commands are allowed.
    - Set PERCEPXION_CLI_WRITE_ENABLED=true to allow write commands.
    - A built-in deny list blocks destructive commands (reload, factory-reset, write erase, etc.)
      even when write is enabled.
    - Set PERCEPXION_CLI_DENY_COMMANDS (comma-separated) to add custom denied commands.
    - Set PERCEPXION_CLI_PERMIT_COMMANDS (comma-separated) for an explicit allowlist.
    - Set PERCEPXION_CLI_YOLO=true to disable all filtering (use with extreme caution).

    Args:
        device_id: Percepxion device ID (from get_device_list).
        command: CLI command string to execute on the device.
        description: Human-readable label stored in the job group record.
        organization_id: Scope to a specific organization. Accepts either a UUID or an exact (case-insensitive) organization name. Falls back to
            PERCEPXION_DEFAULT_ORGANIZATION_ID.
        tenant_id: Deprecated alias for organization_id, kept for backward compatibility.
            Falls back to PERCEPXION_DEFAULT_TENANT_ID if organization_id is not set.
    """
    try:
        check_command(command)
    except CLIPolicyViolation as exc:
        return _err(str(exc))

    logger.info(
        "CLI command dispatched, device_id=%s command=%r correlation_id=%s",
        device_id, command, session.correlation_id,
    )
    effective_tenant_id = _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)
    payload: dict[str, Any] = {
        "name": f"CLI_{device_id[:12]}_{int(time.time())}",
        "description": _audit_description(description),
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
def get_cli_command_output(
    job_group_id: str,
    device_id: str,
    organization_id: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Retrieve the actual CLI output text for a completed send_direct_cli_command job.

    Calls the same endpoint the Percepxion WebUI's device Console/console-editor
    tab uses to render command output (POST /v1/telemetry/result/search,
    type="clicmd"). This endpoint is undocumented in Percepxion's public API
    spec, discovered by reading the WebUI's own source; MQTT only carries job
    *status* ("Completed"/"Failed"), never the output text itself, this call
    is required to get the actual response the device sent.

    Call this after the job's status reaches "Completed" (poll search_job_groups
    or get_job_group first). Calling before completion returns total_results=0,
    not an error, retry after a short delay.

    Args:
        job_group_id: The job_group_id returned by send_direct_cli_command.
        device_id: The Percepxion device ID the command was sent to.
        organization_id: Scope to a specific organization. Accepts either a UUID or an exact (case-insensitive) organization name.
        tenant_id: Deprecated alias for organization_id, kept for backward compatibility.
    """
    payload: dict[str, Any] = {
        "type": "clicmd",
        "job_group_id": [job_group_id],
        "device_id": [device_id],
        "offset": 0,
        "limit": 1,
    }
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
        payload["tenant_id"] = t
    resp = _api_post("/v1/telemetry/result/search", json_body=payload)
    if resp["ok"] and not (resp["data"] or {}).get("total_results"):
        resp["data"]["warning"] = (
            "No result yet for this job_group_id/device_id. The job may still be running, "
            "check status with get_job_group first, or the job may not be a CLI command job."
        )
    return resp


@mcp.tool()
def get_job_results_by_device(
    job_group_id: str,
    result_type: str = "clicmd",
    search_query: str = "",
    limit: int = 50,
    offset: int = 0,
    organization_id: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    List per-device results for a multi-device job group (e.g. a Smart Group
    firmware push, config apply, or CLI command sent to several devices).

    Calls POST /v1/telemetry/result/device/search, the sibling of the
    get_cli_command_output endpoint used for single-device CLI output, without
    a device_id filter it rolls up results across every device targeted by
    the job. Use get_cli_command_output instead when you already know the
    single device_id you want output for.

    Args:
        job_group_id: The job_group_id to look up results for.
        result_type: Job result type, e.g. "clicmd" (CLI commands, default),
            "script", or "command" depending on how the job was created.
        search_query: Optional filter string (e.g. by device name).
        limit: Number of results to return (1-1000).
        offset: Pagination offset.
        organization_id: Scope to a specific organization. Accepts either a UUID or an exact (case-insensitive) organization name.
        tenant_id: Deprecated alias for organization_id, kept for backward compatibility.
    """
    payload: dict[str, Any] = {
        "type": result_type,
        "job_group_id": [job_group_id],
        "offset": max(0, offset),
        "limit": min(max(1, limit), 1000),
    }
    if search_query:
        payload["search_string"] = search_query
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
        payload["tenant_id"] = t
    return _api_post("/v1/telemetry/result/device/search", json_body=payload)


@mcp.tool()
def update_device_config(
    device_id: str,
    items: list[dict[str, str]] | None = None,
    property_name: str | None = None,
    new_value: str | None = None,
    apply_now: bool = True,
    organization_id: str | None = None,
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
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
        save_payload["tenant_id"] = t

    logger.info(
        "Device config update requested, device_id=%s items=%r apply_now=%s",
        device_id, items, apply_now,
    )
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
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
        job_payload["tenant_id"] = t

    job_resp = _api_post("/v1/job/jobgroup/create", json_body=job_payload)
    return _ok({"save": save_resp["data"], "apply_job": job_resp})


def _resolve_template_id(template_name: str, source_device_id: str, organization_id: str | None = None, tenant_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "search_string": template_name,
        "device_id": [source_device_id],
        "offset": 0,
        "limit": 20,
        "sort": "name",
        "order": "desc",
    }
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
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
    organization_id: str | None = None,
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
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
        template_payload["tenant_id"] = t

    logger.info(
        "Config clone requested, source_device_id=%s target_device_id=%s template_name=%s",
        source_device_id, target_device_id, template_name,
    )
    create_resp = _api_post("/v1/telemetry/template/create", json_body=template_payload)
    if not create_resp["ok"]:
        return create_resp

    template_id_resp = _resolve_template_id(
        template_name, source_device_id, organization_id=organization_id, tenant_id=tenant_id
    )
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
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
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
def get_device_firmware_status(device_id: str, organization_id: str | None = None, tenant_id: str | None = None) -> dict[str, Any]:
    """Get device details and summarize firmware version/state."""
    resp = get_device_details(device_id=device_id, organization_id=organization_id, tenant_id=tenant_id)
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
def reboot_device(
    device_id: str,
    description: str = "Reboot requested via MCP",
    organization_id: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Request a device reboot via a Percepxion job group.

    The operation is asynchronous. Use get_job_group or search_job_groups to check status.

    Args:
        device_id: Percepxion device ID (from get_device_list).
        description: Label stored in the job group record.
        organization_id: Scope to a specific organization. Accepts either a UUID or an exact (case-insensitive) organization name.
        tenant_id: Deprecated alias for organization_id, kept for backward compatibility.
    """
    payload: dict[str, Any] = {
        "name": f"Reboot_{device_id[:12]}_{int(time.time())}",
        "description": description,
        "type": "command",
        "subtype": "action",
        "op_code": "execute",
        "operation": "reboot",
        "device_id": [device_id],
        "enable": True,
    }
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
        payload["tenant_id"] = t
    logger.info("Device reboot requested, device_id=%s", device_id)
    return _api_post("/v1/job/jobgroup/create", json_body=payload)


@mcp.tool()
def get_device_config(
    device_id: str,
    selected: bool = True,
    organization_id: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Read the current telemetry config for a device from Percepxion.

    Use this to inspect current settings before calling update_device_config.

    Args:
        device_id: Percepxion device ID (from get_device_list).
        selected: Retained for backward compatibility; ignored. The config/edit
            endpoint always returns the full editable config tree.
        organization_id: Scope to a specific organization. Accepts either a UUID or an exact (case-insensitive) organization name.
        tenant_id: Deprecated alias for organization_id, kept for backward compatibility.
    """
    # /v1/telemetry/config/get is device-token authenticated (device auth_token
    # header) and rejects user tokens with 401 "Invalid device token size".
    # The user-callable endpoint for reading a device's current config is
    # /v1/telemetry/config/edit, verified live 2026-08-27. It returns
    # {device_id, last_fetched, config_record: [{..., field: [{..., value}]}]}.
    payload: dict[str, Any] = {"device_id": device_id}
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
        payload["tenant_id"] = t
    return _api_post("/v1/telemetry/config/edit", json_body=payload)


@mcp.tool()
def request_device_syslog_upload(
    device_ids: list[str],
    log_type: str = "all",
    log_level: str = "info",
    from_date: str | None = None,
    to_date: str | None = None,
    organization_id: str | None = None,
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
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
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
def get_security_telemetry(device_id: str, selected: bool = True, organization_id: str | None = None, tenant_id: str | None = None) -> dict[str, Any]:
    """
    Retrieve full device and per-port telemetry for a Percepxion-managed OOB device.

    This is the canonical source for port-level and managed-device inventory. The
    response contains per-port records (telemetry/port:N/status_record/dp_info) with
    up to 20 fields per managed device: connection status, hostname, model, serial
    number, IP address, OS version, uptime, CPU/memory/flash usage, and more. Also
    includes console manager info, firmware state, network probes, and audit/syslog
    records. Use this tool to answer "what managed devices are attached," "what is on
    port N," or any port-level inventory question. For a single port use
    get_port_telemetry instead to avoid fetching the full payload.

    Args:
        device_id: Percepxion device ID (from get_device_list).
        selected: When True (default), returns only selected/active records.
        organization_id: Scope to a specific organization. Accepts either a UUID or an exact (case-insensitive) organization name.
        tenant_id: Deprecated alias for organization_id, kept for backward compatibility.
    """
    payload: dict[str, Any] = {"device_id": device_id, "selected": selected}
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
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
    organization_id: str | None = None,
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
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
        payload["tenant_id"] = t
    return _api_post("/v1/audit/search", json_body=payload)


@mcp.tool()
def investigate_user_audit_logs(
    user_filter: str = "",
    limit: int = 50,
    offset: int = 0,
    sort: str = "username",
    order: str = "asc",
    organization_id: str | None = None,
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
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
        payload["tenant_id"] = t
    return _api_post("/v1/audit/user/search", json_body=payload)


@mcp.tool()
def set_user_access(
    usernames: list[str],
    enabled: bool,
    organization_id: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Suspend or resume access for one or more Percepxion user accounts (lockdown).

    Use this to disable access during an incident or for offboarding, and to
    restore it afterward. This is the remediation companion to
    investigate_user_audit_logs / investigate_audit_logs: investigate first,
    confirm with the operator, then act. It suspends and resumes only; it does
    NOT create or delete accounts.

    High blast radius: suspending an account cuts off that user's Percepxion
    access fleet-wide. Never call this without explicit operator confirmation
    (same rule as send_direct_cli_command and firmware updates).

    Idempotent: users already in the requested state are reported unchanged and
    left alone. Unknown usernames are reported, not failed.

    Args:
        usernames: Usernames to act on.
        enabled: True to resume (restore) access, False to suspend it.
        organization_id: Scope to a specific organization. Accepts a UUID or an
            exact (case-insensitive) organization name. Falls back to
            PERCEPXION_DEFAULT_ORGANIZATION_ID.
        tenant_id: Deprecated alias for organization_id.
    """
    if not usernames:
        return _err("usernames must be a non-empty list.")

    effective_tenant_id = _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)

    search_payload: dict[str, Any] = {"offset": 0, "limit": 1000, "sort": "username", "order": "asc"}
    if effective_tenant_id:
        search_payload["tenant_id"] = effective_tenant_id
    search = _api_post("/v2/user/search", json_body=search_payload)
    if not search["ok"]:
        return search

    by_name = {u["username"]: u for u in search["data"].get("result", []) if "username" in u}
    requested = list(dict.fromkeys(usernames))  # dedupe, preserve order
    to_change: list[dict[str, Any]] = []
    unchanged: list[str] = []
    not_found: list[str] = []
    for name in requested:
        user = by_name.get(name)
        if user is None:
            not_found.append(name)
        elif bool(user.get("enabled")) == bool(enabled):
            unchanged.append(name)
        else:
            to_change.append({"user_id": user["id"], "name": name})

    action = "resume" if enabled else "suspend"
    logger.warning(
        "User access %s requested via MCP: change=%s unchanged=%s not_found=%s correlation_id=%s",
        action, [u["name"] for u in to_change], unchanged, not_found, session.correlation_id,
    )

    if to_change:
        put_payload: dict[str, Any] = {
            "users": [{"user_id": u["user_id"], "enable": bool(enabled)} for u in to_change],
        }
        if effective_tenant_id:
            put_payload["tenant_id"] = effective_tenant_id
        resp = _api_put("/v1/user", json_body=put_payload)
        if not resp["ok"]:
            return resp

    return _ok({
        "action": action,
        "changed": [u["name"] for u in to_change],
        "unchanged": unchanged,
        "not_found": not_found,
    })


@mcp.tool()
def update_firmware_by_smart_group(
    firmware_file_path: str,
    smart_group_ids: list[str],
    content_name: str,
    version: str,
    description: str = "Firmware update via MCP",
    enable: bool = True,
    organization_id: str | None = None,
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
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
        data_payload["tenant_id"] = t

    try:
        with firmware_path.open("rb") as firmware_file:
            files = {"file": (firmware_path.name, firmware_file, "application/octet-stream")}
            form_data = {"data": json.dumps(data_payload)}
            logger.info(
                "Firmware update requested, content_name=%s version=%s smart_group_ids=%s",
                content_name, version, smart_group_ids,
            )
            return _api_post(
                "/v3/content/create",
                form_data=form_data,
                files=files,
                content_type_json=False,
            )
    except OSError as exc:
        return _err(f"Unable to open firmware file: {exc}")


@mcp.tool()
def list_firmware_content(
    search_query: str = "*",
    limit: int = 50,
    offset: int = 0,
    organization_id: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    List firmware packages already uploaded to Percepxion.

    Use this to check what firmware is available before calling update_firmware_by_smart_group.

    Args:
        search_query: Filter by firmware name. Use '*' for all.
        limit: Number of results (1-1000).
        offset: Pagination offset.
        organization_id: Scope to a specific organization. Accepts either a UUID or an exact (case-insensitive) organization name.
        tenant_id: Deprecated alias for organization_id, kept for backward compatibility.
    """
    payload: dict[str, Any] = {
        "search_string": search_query,
        "type": "firmware",
        "offset": max(0, offset),
        "limit": min(max(1, limit), 1000),
    }
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
        payload["tenant_id"] = t
    return _api_post("/v3/content/search", json_body=payload)


@mcp.tool()
def list_templates(
    search_query: str = "*",
    device_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    organization_id: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    List config templates stored in Percepxion.

    Use this before delete_template or clone_device_config to find existing template names and IDs.

    Args:
        search_query: Filter by template name. Use '*' for all.
        device_id: Filter templates associated with a specific source device.
        limit: Number of results (1-1000).
        offset: Pagination offset.
        organization_id: Scope to a specific organization. Accepts either a UUID or an exact (case-insensitive) organization name.
        tenant_id: Deprecated alias for organization_id, kept for backward compatibility.
    """
    payload: dict[str, Any] = {
        "search_string": search_query,
        "offset": max(0, offset),
        "limit": min(max(1, limit), 1000),
        "sort": "name",
        "order": "asc",
    }
    if device_id:
        payload["device_id"] = [device_id]
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
        payload["tenant_id"] = t
    return _api_post("/v1/telemetry/template/search", json_body=payload)


@mcp.tool()
def delete_template(template_id: str, organization_id: str | None = None, tenant_id: str | None = None) -> dict[str, Any]:
    """
    Delete a config template by ID.

    Use list_templates to find the template ID before deleting.

    Args:
        template_id: The ID of the config template to delete.
        organization_id: Scope to a specific organization. Accepts either a UUID or an exact (case-insensitive) organization name.
        tenant_id: Deprecated alias for organization_id, kept for backward compatibility.
    """
    payload: dict[str, Any] = {"id": template_id}
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
        payload["tenant_id"] = t
    logger.info("Template delete requested, template_id=%s", template_id)
    return _api_post("/v1/telemetry/template/delete", json_body=payload)


@mcp.tool()
def search_job_groups(
    search_string: str = "",
    job_type: str = "command",
    subtype: str | None = None,
    limit: int = 50,
    offset: int = 0,
    organization_id: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Search job groups to monitor asynchronous operation progress."""
    effective_tenant_id = _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)
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
def get_job_group(job_group_id: str, organization_id: str | None = None, tenant_id: str | None = None) -> dict[str, Any]:
    """
    Get full details and output for a specific job group by ID.

    Use this after search_job_groups to retrieve complete job output once you have the ID.
    More reliable than polling by name when multiple jobs share a name prefix.

    Args:
        job_group_id: The job group ID returned by create operations or search_job_groups.
        organization_id: Scope to a specific organization. Accepts either a UUID or an exact (case-insensitive) organization name.
        tenant_id: Deprecated alias for organization_id, kept for backward compatibility.
    """
    payload: dict[str, Any] = {"job_group_id": job_group_id}
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
        payload["tenant_id"] = t
    return _api_post("/v1/job/jobgroup/get", json_body=payload)


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
def list_device_ports(
    device_id: str,
    limit: int = 100,
    offset: int = 0,
    organization_id: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    List serial/device ports on a Percepxion-managed device.

    Returns port names, numbers, and connection state. Use the port data to
    identify targets for serial session access. NOTE: returns port connection
    state only and does not include managed-device attachment details (hostname,
    model, serial, IP, OS version, etc.). Use get_security_telemetry for full
    port and managed-device inventory, or get_port_telemetry for a single port.

    Args:
        device_id: Percepxion device ID (from get_device_list).
        limit: Number of results (1-1000).
        offset: Pagination offset.
        organization_id: Scope to a specific organization. Accepts either a UUID or an exact (case-insensitive) organization name.
        tenant_id: Deprecated alias for organization_id, kept for backward compatibility.
    """
    payload: dict[str, Any] = {
        "search_string": device_id,
        "offset": max(0, offset),
        "limit": min(max(1, limit), 1000),
        "order": "asc",
        "sort": "name",
    }
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id))):
        payload["tenant_id"] = t
    return _api_post("/v3/port/search", json_body=payload)


@mcp.tool()
def get_port_telemetry(
    device_id: str,
    port_number: int,
    organization_id: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Retrieve telemetry for a single serial port, including the attached managed device.

    Returns structured data for one port: connection status, managed-device hostname,
    model, serial number, IP address, OS version, uptime, CPU/memory/flash usage, and
    associated scripts. Much cheaper than get_security_telemetry when only one port is
    needed, filters the full payload server-side and returns a clean object.

    Args:
        device_id: Percepxion device ID (from get_device_list).
        port_number: The port number to query (e.g. 2 for port 2).
        organization_id: Scope to a specific organization. Accepts either a UUID or an exact (case-insensitive) organization name.
        tenant_id: Deprecated alias for organization_id, kept for backward compatibility.
    """
    payload: dict[str, Any] = {"device_id": device_id, "selected": True}
    if (t := _resolve_organization(_effective_org_id(organization_id, tenant_id), required=True)):
        payload["tenant_id"] = t
    raw = _api_post("/v1/telemetry/stat/view", json_body=payload)

    dp_prefix = f"telemetry/port:{port_number}/status_record/dp_info"
    scripts_prefix = f"telemetry/port:{port_number}/scripts"

    managed_device: dict[str, Any] = {}
    scripts: list[Any] = []

    for group in raw.get("status_record", []):
        path = group.get("path", "")
        items = group.get("items", [])
        if path == dp_prefix:
            managed_device = {item["record_name"]: item.get("value") for item in items}
        elif path == scripts_prefix:
            scripts = items

    return {
        "port_number": port_number,
        "managed_device": managed_device,
        "scripts": scripts,
    }


@mcp.tool()
def firmware_compliance_report(
    expected_firmware_version: str,
    search_query: str = "*",
    organization_id: str | None = None,
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
        organization_id=organization_id,
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
    from .config import MCP_HOST, MCP_PORT, MCP_TRANSPORT

    if MCP_TRANSPORT == "sse":
        mcp.run(transport="sse", host=MCP_HOST, port=MCP_PORT)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
