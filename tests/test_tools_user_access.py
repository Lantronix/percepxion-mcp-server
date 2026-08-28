# tests/test_tools_user_access.py
from unittest.mock import patch

from percepxion_mcp import client as cm
from percepxion_mcp.server import set_user_access

USERS = {
    "total": 2,
    "result": [
        {"id": "u-1", "username": "alice", "enabled": True},
        {"id": "u-2", "username": "bob", "enabled": False},
    ],
}


def _mock_user_search(httpserver, users=USERS):
    httpserver.expect_request("/api/v2/user/search", method="POST").respond_with_json(users, status=200)


def test_suspend_enabled_user(authed_session, httpserver):
    _mock_user_search(httpserver)
    httpserver.expect_request("/api/v1/user", method="PUT").respond_with_json({}, status=200)
    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")):
        result = set_user_access(usernames=["alice"], enabled=False)
    assert result["ok"] is True
    assert result["data"]["action"] == "suspend"
    assert result["data"]["changed"] == ["alice"]
    assert result["data"]["unchanged"] == []
    assert result["data"]["not_found"] == []


def test_suspend_already_suspended_is_noop(authed_session, httpserver):
    # No PUT registered: if the tool tried to call it, the request would 500.
    _mock_user_search(httpserver)
    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")):
        result = set_user_access(usernames=["bob"], enabled=False)
    assert result["ok"] is True
    assert result["data"]["changed"] == []
    assert result["data"]["unchanged"] == ["bob"]


def test_resume_suspended_user(authed_session, httpserver):
    _mock_user_search(httpserver)
    httpserver.expect_request("/api/v1/user", method="PUT").respond_with_json({}, status=200)
    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")):
        result = set_user_access(usernames=["bob"], enabled=True)
    assert result["ok"] is True
    assert result["data"]["action"] == "resume"
    assert result["data"]["changed"] == ["bob"]


def test_unknown_user_reported_not_failed(authed_session, httpserver):
    _mock_user_search(httpserver)
    with patch.object(cm, "API_BASE_URL", httpserver.url_for("/api")):
        result = set_user_access(usernames=["ghost"], enabled=False)
    assert result["ok"] is True
    assert result["data"]["not_found"] == ["ghost"]
    assert result["data"]["changed"] == []


def test_empty_usernames_errors(authed_session):
    result = set_user_access(usernames=[], enabled=False)
    assert result["ok"] is False


def test_requires_login():
    # autouse reset_session fixture leaves the session cleared (no tokens).
    result = set_user_access(usernames=["alice"], enabled=False)
    assert result["ok"] is False
