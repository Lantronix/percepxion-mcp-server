# tests/test_token_auth.py
"""Experimental API-token auth mode (proposed backend capability).

The backend does not offer API tokens yet; these tests pin the client-side
contract so the day the platform ships tokens, this server works unchanged:
bearer header, no CSRF, no login call.
"""
from unittest.mock import patch

from percepxion_mcp.client import PercepxionSession, session
from percepxion_mcp.server import login_with_env


def test_api_token_alone_authenticates_session():
    s = PercepxionSession()
    s.api_token = "tok"
    assert s.is_authenticated()


def test_token_mode_headers_bearer_no_csrf():
    s = PercepxionSession()
    s.api_token = "tok"
    h = s.headers()
    assert h["Authorization"] == "Bearer tok"
    assert "x-csrf-token" not in h
    assert "x-mystq-token" not in h


def test_session_mode_headers_unchanged():
    s = PercepxionSession()
    s.auth_token = "t"
    s.csrf_token = "c"
    h = s.headers()
    assert h["x-mystq-token"] == "t"
    assert h["x-csrf-token"] == "c"
    assert "Authorization" not in h


def test_clear_resets_api_token():
    s = PercepxionSession()
    s.api_token = "tok"
    s.clear()
    assert not s.is_authenticated()


def test_login_with_env_skips_login_post_in_token_mode(monkeypatch):
    monkeypatch.setenv("PERCEPXION_API_TOKEN", "tok-abc")
    try:
        with patch(
            "percepxion_mcp.server._api_post",
            side_effect=AssertionError("login POST must not happen in token mode"),
        ):
            result = login_with_env()
        assert result["ok"] is True
        assert result["data"]["auth_mode"] == "api_token"
        assert session.api_token == "tok-abc"
        assert session.trust_harvested_organizations is True
        assert session.headers()["Authorization"] == "Bearer tok-abc"
    finally:
        session.clear()
