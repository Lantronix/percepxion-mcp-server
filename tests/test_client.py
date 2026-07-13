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

def test_resolve_organization_caller_supplied():
    assert _resolve_organization("caller-id") == "caller-id"


def test_resolve_organization_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(client_mod, "DEFAULT_ORGANIZATION_ID", "default-id")
    assert _resolve_organization(None) == "default-id"


def test_resolve_organization_none_when_no_default(monkeypatch):
    monkeypatch.setattr(client_mod, "DEFAULT_ORGANIZATION_ID", None)
    assert _resolve_organization(None) is None


def test_resolve_tenant_is_deprecated_alias_for_resolve_organization(monkeypatch):
    """_resolve_tenant is kept as a backward-compatible alias for _resolve_organization."""
    monkeypatch.setattr(client_mod, "DEFAULT_ORGANIZATION_ID", "default-id")
    assert _resolve_tenant("caller-id") == "caller-id"
    assert _resolve_tenant(None) == "default-id"


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
