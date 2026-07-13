# tests/test_tools_core.py
import pytest
from unittest.mock import patch
from percepxion_mcp import client as cm
from percepxion_mcp import cli_policy
from percepxion_mcp.server import (
    send_direct_cli_command,
    update_firmware_by_smart_group,
    firmware_compliance_report,
    get_device_list,
    get_job_group,
    list_device_ports,
    list_organizations,
    list_tenants,
    get_devices_by_organization,
    login_with_env,
)


# --- login_with_env / permitted_organization_ids capture ---

def _mock_login(monkeypatch, httpserver, user_payload=None):
    monkeypatch.setenv("PERCEPXION_USERNAME", "test-user")
    monkeypatch.setenv("PERCEPXION_PASSWORD", "test-pass")
    login_response = {"token": "tok-123", "csrf_token": "csrf-123"}
    if user_payload is not None:
        login_response["user"] = user_payload
    httpserver.expect_request("/api/v2/user/login", method="POST").respond_with_json(
        login_response, status=200
    )


def test_login_with_env_populates_permitted_organization_ids(monkeypatch, httpserver):
    user_payload = {
        "group": [
            {"id": "g1", "name": "Org Admins", "tenant_id": "dddddddd-0000-0000-0000-000000000001"},
            {"id": "g2", "name": "Org Viewers", "tenant_id": "dddddddd-0000-0000-0000-000000000002"},
        ]
    }
    _mock_login(monkeypatch, httpserver, user_payload)
    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")):
        result = login_with_env()
    assert result["ok"] is True
    assert cm.session.permitted_organization_ids == {
        "dddddddd-0000-0000-0000-000000000001",
        "dddddddd-0000-0000-0000-000000000002",
    }


def test_login_with_env_degrades_gracefully_when_group_missing(monkeypatch, httpserver):
    """No user.group in the login response should not crash login; permitted set stays empty."""
    _mock_login(monkeypatch, httpserver, user_payload={})
    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")):
        result = login_with_env()
    assert result["ok"] is True
    assert cm.session.permitted_organization_ids == set()


def test_login_with_env_degrades_gracefully_when_user_key_missing(monkeypatch, httpserver):
    """No 'user' key at all in the login response should not crash login."""
    _mock_login(monkeypatch, httpserver, user_payload=None)
    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")):
        result = login_with_env()
    assert result["ok"] is True
    assert cm.session.permitted_organization_ids == set()


# --- send_direct_cli_command ---

def test_cli_command_blocked_by_policy(authed_session):
    """Read-only mode (default) blocks write commands."""
    result = send_direct_cli_command(device_id="dev-001", command="set hostname foo")
    assert result["ok"] is False
    assert "Write commands are disabled" in result["error"]


def test_cli_command_denied_command_blocked(authed_session):
    """Denied commands are blocked even when write is enabled."""
    with patch.object(cli_policy, "_CLI_WRITE_ENABLED", True):
        result = send_direct_cli_command(device_id="dev-001", command="reload")
    assert result["ok"] is False
    assert "deny list" in result["error"]


def test_cli_command_read_command_dispatched(authed_session, httpserver):
    """Read commands are dispatched to Percepxion in read-only mode."""
    httpserver.expect_request("/api/v1/job/jobgroup/create", method="POST").respond_with_json(
        {"id": "job-123", "status": "created"}, status=200
    )
    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")):
        result = send_direct_cli_command(device_id="dev-001", command="show version")
    assert result["ok"] is True


# --- update_firmware_by_smart_group path traversal ---

def test_firmware_upload_rejects_path_outside_allowed_dir(authed_session, tmp_path):
    """Firmware path traversal guard."""
    safe_dir = tmp_path / "firmware"
    safe_dir.mkdir()
    evil_file = tmp_path / "evil.bin"
    evil_file.write_bytes(b"\x00" * 100)

    import percepxion_mcp.server as server_mod
    with patch.object(server_mod, "FIRMWARE_DIR", str(safe_dir)):
        result = update_firmware_by_smart_group(
            firmware_file_path=str(evil_file),
            smart_group_ids=["sg-1"],
            content_name="Evil Firmware",
            version="9.9.9",
        )
    assert result["ok"] is False
    assert "outside the allowed directory" in result["error"]


def test_firmware_upload_accepts_path_within_allowed_dir(authed_session, tmp_path, httpserver):
    """Valid firmware path within allowed dir is accepted."""
    safe_dir = tmp_path / "firmware"
    safe_dir.mkdir()
    fw_file = safe_dir / "fw.bin"
    fw_file.write_bytes(b"\x00" * 100)

    httpserver.expect_request("/api/v3/content/create", method="POST").respond_with_json(
        {"id": "content-1"}, status=200
    )
    import percepxion_mcp.server as server_mod
    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")), \
         patch.object(server_mod, "FIRMWARE_DIR", str(safe_dir)):
        result = update_firmware_by_smart_group(
            firmware_file_path=str(fw_file),
            smart_group_ids=["sg-1"],
            content_name="Good Firmware",
            version="9.7.0",
        )
    assert result["ok"] is True


# --- firmware_compliance_report ---

def test_compliance_report_categorizes_devices(authed_session, httpserver):
    """Compliance report correctly splits compliant/non_compliant/unknown."""
    httpserver.expect_request("/api/v3/device/search", method="POST").respond_with_json({
        "search_results": [
            {"device_id": "d1", "device_name": "Router-A", "attributes": {"firmware_ver": "9.7.0", "model": "SLC9016"}, "serial_num": "SN001", "status": "online", "last_contacted": "2026-05-01"},
            {"device_id": "d2", "device_name": "Router-B", "attributes": {"firmware_ver": "9.6.0", "model": "SLC9016"}, "serial_num": "SN002", "status": "online", "last_contacted": "2026-05-01"},
            {"device_id": "d3", "device_name": "Router-C", "attributes": {"firmware_ver": None, "model": "SLC9016"}, "serial_num": "SN003", "status": "offline", "last_contacted": None},
        ]
    }, status=200)
    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")):
        result = firmware_compliance_report(expected_firmware_version="9.7.0")
    assert result["ok"] is True
    data = result["data"]
    assert data["compliant_count"] == 1
    assert data["non_compliant_count"] == 1
    assert data["unknown_count"] == 1
    assert data["compliance_percent"] == pytest.approx(33.33, abs=0.01)


# --- get_job_group ---

def test_get_job_group_requires_auth():
    result = get_job_group(job_group_id="jg-001")
    assert result["ok"] is False
    assert "login_with_env" in result["error"]


def test_get_job_group_sends_job_group_id_key(authed_session):
    """Regression: payload must use 'job_group_id', not 'id' (API returns VALIDATION_ERROR otherwise)."""
    from unittest.mock import patch, MagicMock
    import requests

    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"job_group_id": "jg-001", "status": "completed"}

    with patch("requests.post", return_value=mock_resp) as mock_post:
        result = get_job_group(job_group_id="jg-001")

    assert result["ok"] is True
    _, kwargs = mock_post.call_args
    sent_body = kwargs.get("json", {})
    assert "job_group_id" in sent_body, "payload must use key 'job_group_id'"
    assert "id" not in sent_body, "payload must not use legacy key 'id'"
    assert sent_body["job_group_id"] == "jg-001"


def test_get_job_group_returns_job_data(authed_session, httpserver):
    """Happy path: get_job_group returns full job group details."""
    job_data = {"job_group_id": "jg-002", "status": "completed", "jobs": [{"output": "show version..."}]}
    httpserver.expect_request("/api/v1/job/jobgroup/get", method="POST").respond_with_json(job_data, status=200)
    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")):
        result = get_job_group(job_group_id="jg-002")
    assert result["ok"] is True
    assert result["data"]["job_group_id"] == "jg-002"
    assert result["data"]["status"] == "completed"


# --- get_device_list ---

def test_get_device_list_requires_auth():
    result = get_device_list()
    assert result["ok"] is False
    assert "login_with_env" in result["error"]


# --- list_device_ports ---

def test_list_device_ports_requires_auth():
    result = list_device_ports(device_id="dev-001")
    assert result["ok"] is False
    assert "login_with_env" in result["error"]


def test_list_device_ports_sends_search_string_key(authed_session):
    """Regression: payload must use 'search_string' with the device_id value, not 'device_id' key."""
    from unittest.mock import MagicMock
    import requests

    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"total": 0, "result": []}

    with patch("requests.post", return_value=mock_resp) as mock_post:
        result = list_device_ports(device_id="dev-abc")

    assert result["ok"] is True
    _, kwargs = mock_post.call_args
    sent_body = kwargs.get("json", {})
    assert "search_string" in sent_body, "payload must use key 'search_string'"
    assert "device_id" not in sent_body, "payload must not use legacy key 'device_id'"
    assert sent_body["search_string"] == "dev-abc"


# --- organization_id / tenant_id (deprecated alias) rename ---
#
# UUID-shaped values are used for these passthrough tests since a non-UUID
# value now triggers organization-name resolution (see the dedicated
# name-resolution tests further down).

ORG_ABC_UUID = "aaaaaaaa-0000-0000-0000-000000000001"
TENANT_LEGACY_UUID = "aaaaaaaa-0000-0000-0000-000000000002"
ORG_WINS_UUID = "aaaaaaaa-0000-0000-0000-000000000003"
TENANT_LOSES_UUID = "aaaaaaaa-0000-0000-0000-000000000004"
DEFAULT_ORG_UUID = "aaaaaaaa-0000-0000-0000-000000000005"


def test_organization_id_param_sent_as_tenant_id_in_payload(authed_session, httpserver):
    """New organization_id param resolves and is still sent as 'tenant_id' (API constraint)."""
    httpserver.expect_request("/api/v3/device/search", method="POST").respond_with_json(
        {"search_results": []}, status=200
    )
    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")):
        result = get_device_list(organization_id=ORG_ABC_UUID)
    assert result["ok"] is True
    req = httpserver.log[-1][0]
    assert req.get_json()["tenant_id"] == ORG_ABC_UUID


def test_legacy_tenant_id_param_still_works(authed_session, httpserver):
    httpserver.expect_request("/api/v3/device/search", method="POST").respond_with_json(
        {"search_results": []}, status=200
    )
    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")):
        result = get_device_list(tenant_id=TENANT_LEGACY_UUID)
    assert result["ok"] is True
    req = httpserver.log[-1][0]
    assert req.get_json()["tenant_id"] == TENANT_LEGACY_UUID


def test_organization_id_takes_precedence_over_legacy_tenant_id(authed_session, httpserver):
    httpserver.expect_request("/api/v3/device/search", method="POST").respond_with_json(
        {"search_results": []}, status=200
    )
    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")):
        result = get_device_list(organization_id=ORG_WINS_UUID, tenant_id=TENANT_LOSES_UUID)
    assert result["ok"] is True
    req = httpserver.log[-1][0]
    assert req.get_json()["tenant_id"] == ORG_WINS_UUID


def test_default_organization_id_env_fallback_used_when_neither_supplied(authed_session, httpserver):
    httpserver.expect_request("/api/v3/device/search", method="POST").respond_with_json(
        {"search_results": []}, status=200
    )
    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")), \
         patch.object(cm, "DEFAULT_ORGANIZATION_ID", DEFAULT_ORG_UUID):
        result = get_device_list()
    assert result["ok"] is True
    req = httpserver.log[-1][0]
    assert req.get_json()["tenant_id"] == DEFAULT_ORG_UUID


# --- list_organizations / list_tenants ---
#
# Rewritten: /v1/tenant/search does not exist server-side (400s in production).
# IDs now come from session.permitted_organization_ids (login-derived RBAC);
# names are best-effort, resolved by scanning visible devices via
# /v3/device/search.

def test_list_organizations_uses_permitted_ids_and_device_derived_names(authed_session, httpserver):
    """list_organizations is primary; list_tenants is a deprecated alias with identical behavior."""
    org_id = "bbbbbbbb-0000-0000-0000-000000000001"
    authed_session.permitted_organization_ids = {org_id}
    httpserver.expect_request("/api/v3/device/search", method="POST").respond_with_json(
        {"search_results": [{"device_id": "d1", "tenant": [{"id": org_id, "name": "Acme"}]}]},
        status=200,
    )
    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")):
        org_result = list_organizations()
    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")):
        tenant_result = list_tenants()
    assert org_result["ok"] is True
    assert tenant_result["ok"] is True
    assert org_result["data"] == tenant_result["data"]
    assert org_result["data"]["organizations"] == [{"organization_id": org_id, "name": "Acme"}]


def test_list_organizations_shows_permitted_org_with_no_visible_devices(authed_session, httpserver):
    """An org with zero visible devices still appears (permission-derived ID), name is None."""
    permitted_id = "bbbbbbbb-0000-0000-0000-000000000002"
    authed_session.permitted_organization_ids = {permitted_id}
    httpserver.expect_request("/api/v3/device/search", method="POST").respond_with_json(
        {"search_results": []}, status=200
    )
    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")):
        result = list_organizations()
    assert result["ok"] is True
    assert result["data"]["organizations"] == [{"organization_id": permitted_id, "name": None}]


def test_list_organizations_empty_permitted_set_returns_warning(authed_session):
    authed_session.permitted_organization_ids = set()
    result = list_organizations()
    assert result["ok"] is True
    assert result["data"]["organizations"] == []
    assert "warning" in result["data"]


def test_list_organizations_requires_auth():
    result = list_organizations()
    assert result["ok"] is False
    assert "login_with_env" in result["error"]


def test_get_devices_by_organization_requires_an_id():
    result = get_devices_by_organization()
    assert result["ok"] is False
    assert "organization_id" in result["error"]


def test_get_devices_by_organization_accepts_organization_id(authed_session, httpserver):
    httpserver.expect_request("/api/v3/device/search", method="POST").respond_with_json(
        {"search_results": []}, status=200
    )
    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")):
        result = get_devices_by_organization(organization_id=ORG_ABC_UUID)
    assert result["ok"] is True
    req = httpserver.log[-1][0]
    assert req.get_json()["tenant_id"] == ORG_ABC_UUID


def test_get_devices_by_organization_accepts_legacy_tenant_id(authed_session, httpserver):
    httpserver.expect_request("/api/v3/device/search", method="POST").respond_with_json(
        {"search_results": []}, status=200
    )
    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")):
        result = get_devices_by_organization(tenant_id=TENANT_LEGACY_UUID)
    assert result["ok"] is True
    req = httpserver.log[-1][0]
    assert req.get_json()["tenant_id"] == TENANT_LEGACY_UUID


# --- end-to-end: organization name resolves before hitting the target endpoint ---

def test_get_device_list_resolves_organization_name_end_to_end(authed_session, httpserver):
    """Calling a real tool with a name instead of a UUID resolves it via
    /v3/device/search (permission-scoped) before the tool's own device-search call."""
    org_id = "cccccccc-0000-0000-0000-000000000001"
    authed_session.permitted_organization_ids = {org_id}

    # First call: the harvesting/resolution pass used to resolve "Acme Corp" -> org_id.
    # Second call: the actual get_device_list search, scoped by the resolved tenant_id.
    httpserver.expect_ordered_request("/api/v3/device/search", method="POST").respond_with_json(
        {"search_results": [{"device_id": "d1", "tenant": [{"id": org_id, "name": "Acme Corp"}]}]},
        status=200,
    )
    httpserver.expect_ordered_request("/api/v3/device/search", method="POST").respond_with_json(
        {"search_results": [{"device_id": "d1", "device_name": "Router-A"}]},
        status=200,
    )

    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")):
        result = get_device_list(organization_id="Acme Corp")

    assert result["ok"] is True
    # Final call must have been scoped to the resolved UUID, not the raw name.
    req = httpserver.log[-1][0]
    assert req.get_json()["tenant_id"] == org_id


def test_list_device_ports_returns_port_data(authed_session, httpserver):
    """Happy path: list_device_ports returns paginated port records from /v3/port/search."""
    port_data = {
        "total": 2,
        "result": [
            {"name": "Port-1", "port_number": 1, "status": "Connected", "parent_device_id": "dev-xyz"},
            {"name": "Port-2", "port_number": 2, "status": "Disconnected", "parent_device_id": "dev-xyz"},
        ],
    }
    httpserver.expect_request("/api/v3/port/search", method="POST").respond_with_json(port_data, status=200)
    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")):
        result = list_device_ports(device_id="dev-xyz")
    assert result["ok"] is True
    assert result["data"]["total"] == 2
    assert result["data"]["result"][0]["name"] == "Port-1"
    assert result["data"]["result"][1]["status"] == "Disconnected"
