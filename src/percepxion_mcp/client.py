# src/percepxion_mcp/client.py
import logging
import uuid
from typing import Any

import requests

from .config import API_BASE_URL, DEFAULT_ORGANIZATION_ID, REQUEST_TIMEOUT

logger = logging.getLogger("percepxion_mcp")


class OrganizationResolutionError(Exception):
    """
    Raised when an organization display name cannot be resolved to exactly one
    permitted organization_id. Mirrors the CLIPolicyViolation exception pattern
    used elsewhere in this codebase (cli_policy.py); callers that want a
    structured {"ok": False, ...} response should catch this explicitly.
    """


class PercepxionSession:
    """Stores user authentication headers for the current MCP process."""

    def __init__(self) -> None:
        self.auth_token: str | None = None
        self.csrf_token: str | None = None
        # organization_ids the authenticated user has RBAC permission for,
        # captured from user.group[].tenant_id (and, for tenant_admin, the
        # top-level user.tenant_id) in the /v2/user/login response. This is
        # the authoritative permission boundary for name-based organization
        # lookup for tenant_user/tenant_admin roles: a name match against an
        # org outside this set must never be returned.
        self.permitted_organization_ids: set[str] = set()
        # True when the authenticated user's role (project_admin) grants
        # access to an entire Project's worth of organizations that Percepxion
        # has no endpoint to enumerate directly. group[]/tenant_id are empty
        # by design for this role, so permitted_organization_ids alone would
        # incorrectly read as "zero organizations" for a real admin. When
        # True, org visibility instead trusts whatever /v3/device/search
        # actually returns for this session, since the backend already scopes
        # that endpoint's results to what the authenticated user can see.
        self.trust_harvested_organizations: bool = False

    def is_authenticated(self) -> bool:
        return bool(self.auth_token and self.csrf_token)

    def clear(self) -> None:
        self.auth_token = None
        self.csrf_token = None
        self.permitted_organization_ids = set()
        self.trust_harvested_organizations = False

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


def _is_uuid(value: str) -> bool:
    """Return True if value parses as a UUID. Used to distinguish an
    organization_id (UUID) from an organization display name."""
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


# Bounded pagination guard for organization-name harvesting via /v3/device/search.
# Keeps name resolution/listing from silently looping forever on huge fleets.
_ORG_CANDIDATE_MAX_PAGES = 5
_ORG_CANDIDATE_PAGE_SIZE = 200


def _harvest_organization_candidates() -> dict[str, str]:
    """
    Page through /v3/device/search (bounded) and harvest distinct
    {organization_id: organization_name} pairs from each device's embedded
    tenant[] list.

    There is no working organization/tenant listing endpoint in the Percepxion
    API (/v1/tenant/search is referenced in the API spec but not implemented
    server-side; it 400s in production). Device search is the only endpoint
    that surfaces organization display names, embedded per-device as
    tenant: [{"id": ..., "name": ...}, ...]. This means organizations with
    zero visible devices will not have a resolvable name.
    """
    candidates: dict[str, str] = {}
    search_after: list[str] | None = None
    for _ in range(_ORG_CANDIDATE_MAX_PAGES):
        payload: dict[str, Any] = {
            "search_string": "*",
            "limit": _ORG_CANDIDATE_PAGE_SIZE,
            "sort": "device_name",
            "order": "asc",
        }
        if search_after:
            payload["search_after"] = search_after
            payload["pagination"] = "next"

        resp = _api_post("/v3/device/search", json_body=payload)
        if not resp["ok"]:
            raise OrganizationResolutionError(
                f"Device search failed while resolving organization names: {resp.get('error')}"
            )

        data = resp["data"] or {}
        results = data.get("search_results") or []
        for device in results:
            for tenant in device.get("tenant") or []:
                tenant_id = tenant.get("id")
                tenant_name = tenant.get("name")
                if tenant_id and tenant_name:
                    candidates[tenant_id] = tenant_name

        sort_last = data.get("sort_last")
        if not sort_last or not results or len(results) < _ORG_CANDIDATE_PAGE_SIZE:
            break
        search_after = sort_last

    return candidates


def resolve_organization_by_name(name: str) -> str:
    """
    Resolve an organization display name to its organization_id (UUID).

    Matching is case-insensitive and exact (no substring/fuzzy matching).
    Every candidate is cross-checked against session.permitted_organization_ids
    (the authenticated user's own login-derived RBAC permissions) before being
    accepted; a name match for an organization outside that set is rejected,
    not returned. This is a hard security boundary: name-based lookup must
    never become a way to discover organizations the caller isn't already
    entitled to.

    Exception: when session.trust_harvested_organizations is True (role ==
    project_admin), permitted_organization_ids is empty by design (that role's
    access isn't expressed via group[]), so any candidate actually harvested
    from /v3/device/search is trusted instead, that endpoint is already
    backend-scoped to devices the authenticated session can see.

    Raises OrganizationResolutionError if there are zero or multiple matches
    among the caller's permitted organizations.
    """
    candidates = _harvest_organization_candidates()
    permitted = session.permitted_organization_ids
    trust_harvested = session.trust_harvested_organizations
    target = name.strip().lower()

    matches = sorted(
        org_id
        for org_id, org_name in candidates.items()
        if org_name.strip().lower() == target and (org_id in permitted or trust_harvested)
    )

    if not matches:
        scope_note = (
            "your project's visible devices"
            if trust_harvested
            else f"your permitted organizations ({len(permitted)} available)"
        )
        raise OrganizationResolutionError(
            f"No organization named '{name}' found among {scope_note}, "
            f"{len(candidates)} organization(s) have a resolvable name from visible devices. "
            "Name matching only covers organizations with at least one visible device. "
            "Use the organization_id (UUID) directly instead, or call "
            "list_organizations to see permitted organization_ids."
        )
    if len(matches) > 1:
        raise OrganizationResolutionError(
            f"Organization name '{name}' is ambiguous, matched {len(matches)} permitted "
            f"organization_ids: {', '.join(matches)}. Use the organization_id (UUID) directly instead."
        )
    return matches[0]


def _resolve_organization(organization_id: str | None) -> str | None:
    """
    Return caller-supplied organization_id, or the configured default.

    If the resulting value is present and NOT UUID-shaped, it's treated as an
    organization display name and resolved to a UUID via
    resolve_organization_by_name(), scoped to the authenticated user's own
    permitted organizations. A UUID-shaped value skips resolution entirely
    (pure passthrough, no extra API call), exactly as before this feature
    existed.
    """
    value = organization_id
    if not value:
        if DEFAULT_ORGANIZATION_ID:
            logger.warning(
                "No organization_id supplied by caller, falling back to "
                "PERCEPXION_DEFAULT_ORGANIZATION_ID=%s",
                DEFAULT_ORGANIZATION_ID,
            )
        value = DEFAULT_ORGANIZATION_ID

    if value and not _is_uuid(value):
        return resolve_organization_by_name(value)
    return value


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
