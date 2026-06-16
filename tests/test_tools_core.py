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
)


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
