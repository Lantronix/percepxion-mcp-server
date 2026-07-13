# tests/test_client.py
import pytest
from unittest.mock import patch
from percepxion_mcp import client as client_mod
from percepxion_mcp.client import (
    PercepxionSession,
    _ok,
    _err,
    _resolve_tenant,
    _resolve_organization,
    _api_post,
)


# --- PercepxionSession ---

def test_session_unauthenticated_by_default():
    s = PercepxionSession()
    assert not s.is_authenticated()


def test_session_authenticated_after_tokens_set():
    s = PercepxionSession()
    s.auth_token = "tok"
    s.csrf_token = "csrf"
    assert s.is_authenticated()


def test_session_clear_removes_tokens():
    s = PercepxionSession()
    s.auth_token = "tok"
    s.csrf_token = "csrf"
    s.clear()
    assert not s.is_authenticated()


def test_session_headers_include_auth_tokens(authed_session):
    h = authed_session.headers()
    assert h["x-mystq-token"] == "fake-auth-token"
    assert h["x-csrf-token"] == "fake-csrf-token"


def test_session_headers_no_auth_tokens_when_unauthenticated():
    s = PercepxionSession()
    h = s.headers()
    assert "x-mystq-token" not in h
    assert "x-csrf-token" not in h


# --- _ok / _err ---

def test_ok_structure():
    result = _ok({"foo": "bar"})
    assert result["ok"] is True
    assert result["data"] == {"foo": "bar"}


def test_ok_with_status_code():
    result = _ok({}, 200)
    assert result["status_code"] == 200


def test_err_structure():
    result = _err("something failed")
    assert result["ok"] is False
    assert result["error"] == "something failed"


def test_err_with_details():
    result = _err("oops", 400, {"detail": "bad request"})
    assert result["status_code"] == 400
    assert result["details"] == {"detail": "bad request"}


# --- _resolve_organization / _resolve_tenant (deprecated alias) ---
#
# UUID-shaped values are used here since they exercise the pure-passthrough
# path (no organization-name resolution / HTTP call). Non-UUID values are
# covered separately below under organization-name resolution tests.

CALLER_UUID = "11111111-1111-1111-1111-111111111111"
DEFAULT_UUID = "22222222-2222-2222-2222-222222222222"


def test_resolve_organization_caller_supplied():
    assert _resolve_organization(CALLER_UUID) == CALLER_UUID


def test_resolve_organization_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(client_mod, "DEFAULT_ORGANIZATION_ID", DEFAULT_UUID)
    assert _resolve_organization(None) == DEFAULT_UUID


def test_resolve_organization_none_when_no_default(monkeypatch):
    monkeypatch.setattr(client_mod, "DEFAULT_ORGANIZATION_ID", None)
    assert _resolve_organization(None) is None


def test_resolve_tenant_is_deprecated_alias_for_resolve_organization(monkeypatch):
    """_resolve_tenant is kept as a backward-compatible alias for _resolve_organization."""
    monkeypatch.setattr(client_mod, "DEFAULT_ORGANIZATION_ID", DEFAULT_UUID)
    assert _resolve_tenant(CALLER_UUID) == CALLER_UUID
    assert _resolve_tenant(None) == DEFAULT_UUID


# --- organization-name resolution (resolve_organization_by_name / UUID passthrough) ---


def _device_search_response(httpserver, tenants):
    """Mock a single-page /v3/device/search response with one device per tenant."""
    results = [
        {
            "device_id": f"dev-{i}",
            "tenant": [{"id": tid, "name": tname}],
        }
        for i, (tid, tname) in enumerate(tenants)
    ]
    httpserver.expect_request("/api/v3/device/search", method="POST").respond_with_json(
        {"search_results": results}, status=200
    )


def test_resolve_organization_by_name_single_exact_match(authed_session, httpserver):
    org_id = "33333333-3333-3333-3333-333333333333"
    authed_session.permitted_organization_ids = {org_id}
    _device_search_response(httpserver, [(org_id, "Acme Corp")])
    with patch.object(client_mod, "API_BASE_URL", httpserver.url_for("/api")):
        result = _resolve_organization("acme corp")  # case-insensitive
    assert result == org_id


def test_resolve_organization_by_name_no_matches_raises(authed_session, httpserver):
    authed_session.permitted_organization_ids = {"33333333-3333-3333-3333-333333333333"}
    _device_search_response(httpserver, [("33333333-3333-3333-3333-333333333333", "Acme Corp")])
    with patch.object(client_mod, "API_BASE_URL", httpserver.url_for("/api")):
        with pytest.raises(client_mod.OrganizationResolutionError):
            _resolve_organization("Nonexistent Org")


def test_resolve_organization_by_name_ambiguous_raises(authed_session, httpserver):
    id_a = "44444444-4444-4444-4444-444444444444"
    id_b = "55555555-5555-5555-5555-555555555555"
    authed_session.permitted_organization_ids = {id_a, id_b}
    _device_search_response(httpserver, [(id_a, "Duplicate Name"), (id_b, "Duplicate Name")])
    with patch.object(client_mod, "API_BASE_URL", httpserver.url_for("/api")):
        with pytest.raises(client_mod.OrganizationResolutionError):
            _resolve_organization("Duplicate Name")


def test_resolve_organization_uuid_shaped_input_skips_resolution_entirely(authed_session):
    """A UUID-shaped value must never trigger a device-search HTTP call."""
    org_id = "66666666-6666-6666-6666-666666666666"
    with patch("requests.post") as mock_post:
        result = _resolve_organization(org_id)
    assert result == org_id
    mock_post.assert_not_called()


def test_resolve_organization_by_name_rejects_match_outside_permitted_ids(authed_session, httpserver):
    """A device-derived name match whose tenant.id is NOT in permitted_organization_ids
    must be rejected, not returned. This is the security control."""
    visible_but_not_permitted = "77777777-7777-7777-7777-777777777777"
    authed_session.permitted_organization_ids = {"88888888-8888-8888-8888-888888888888"}
    _device_search_response(httpserver, [(visible_but_not_permitted, "Acme Corp")])
    with patch.object(client_mod, "API_BASE_URL", httpserver.url_for("/api")):
        with pytest.raises(client_mod.OrganizationResolutionError):
            _resolve_organization("Acme Corp")


# --- _api_post ---

def test_api_post_returns_error_when_unauthenticated():
    result = _api_post("/v1/test")
    assert result["ok"] is False
    assert "login_with_env" in result["error"]


def test_api_post_handles_401_and_clears_session(authed_session, httpserver):
    httpserver.expect_request("/api/v1/test", method="POST").respond_with_json(
        {"error": "Unauthorized"}, status=401
    )
    with patch.object(client_mod, "API_BASE_URL", httpserver.url_for("/api")):
        result = _api_post("/v1/test")
    assert result["ok"] is False
    assert result["status_code"] == 401
    assert not authed_session.is_authenticated()


def test_api_post_handles_network_error(authed_session):
    with patch.object(client_mod, "API_BASE_URL", "http://127.0.0.1:1"):
        result = _api_post("/v1/test")
    assert result["ok"] is False
    assert "Request failed" in result["error"]


def test_api_post_handles_non_json_response(authed_session, httpserver):
    httpserver.expect_request("/api/v1/test", method="POST").respond_with_data(
        "plain text response", content_type="text/plain"
    )
    with patch.object(client_mod, "API_BASE_URL", httpserver.url_for("/api")):
        result = _api_post("/v1/test")
    assert result["ok"] is True
    assert "raw_text" in result["data"]


def test_api_post_200_returns_ok(authed_session, httpserver):
    httpserver.expect_request("/api/v1/test", method="POST").respond_with_json(
        {"result": "good"}, status=200
    )
    with patch.object(client_mod, "API_BASE_URL", httpserver.url_for("/api")):
        result = _api_post("/v1/test")
    assert result["ok"] is True
    assert result["data"] == {"result": "good"}
