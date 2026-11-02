"""Client authentication, start-up checks, Host/Origin validation and firmware
path confinement for the remote transports."""
from __future__ import annotations

import asyncio
import logging
import os
from unittest.mock import patch

import httpx
import pytest

import percepxion_mcp.server as server_mod
from percepxion_mcp.remote_security import (
    RemoteConfigError,
    RemoteSettings,
    check_remote_settings,
    load_auth_token,
    settings_from_env,
)
from percepxion_mcp.server import build_http_app, update_firmware_by_smart_group

TOKEN = "s3cret-token-for-tests-0123456789"
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _bound_to(app, addr):
    """Present the scope a real uvicorn listener on addr would (httpx's test
    transport otherwise reports the request's own Host as the server address)."""

    async def shim(scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "server": addr}
        await app(scope, receive, send)

    return shim


async def _post(app, headers, host="127.0.0.1:8765", bind=None):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=_bound_to(app, bind) if bind else app)
        async with httpx.AsyncClient(transport=transport, base_url=f"http://{host}") as c:
            return await c.post("/mcp", json=INIT, headers={**HEADERS, **headers})


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=20))


# --- 1. bearer-token authentication ---------------------------------------


def test_token_required_on_mcp():
    app = build_http_app("both", auth_token=TOKEN)
    r = _run(_post(app, {}))
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Bearer")


def test_wrong_token_refused():
    app = build_http_app("both", auth_token=TOKEN)
    assert _run(_post(app, {"Authorization": "Bearer " + TOKEN[:-1] + "x"})).status_code == 401
    assert _run(_post(app, {"Authorization": "Basic " + TOKEN})).status_code == 401


def test_right_token_accepted():
    app = build_http_app("both", auth_token=TOKEN)
    r = _run(_post(app, {"Authorization": "Bearer " + TOKEN}))
    assert r.status_code == 200
    assert "Percepxion-Server" in r.text


def test_token_also_guards_sse_routes():
    app = build_http_app("both", auth_token=TOKEN)

    async def go():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
                a = await c.post("/messages/?session_id=x", json={})
                return a.status_code

    assert _run(go()) == 401


def test_token_from_file(tmp_path):
    f = tmp_path / "token"
    f.write_text(TOKEN + "\n")
    assert load_auth_token({"MCP_AUTH_TOKEN_FILE": str(f)}) == TOKEN


def test_short_token_rejected():
    with pytest.raises(RemoteConfigError, match="16"):
        load_auth_token({"MCP_AUTH_TOKEN": "short"})


def test_remote_bind_without_token_refuses_to_start():
    s = RemoteSettings("both", "0.0.0.0", None, allowed_hosts=["mcp.example.com"])
    with pytest.raises(RemoteConfigError, match="MCP_AUTH_TOKEN"):
        check_remote_settings(s)


def test_loopback_without_token_is_allowed():
    check_remote_settings(RemoteSettings("both", "127.0.0.1", None))


def test_explicit_unauthenticated_opt_out_warns(caplog):
    s = RemoteSettings("http", "0.0.0.0", None, allow_unauthenticated=True, allowed_hosts=["h"])
    with caplog.at_level(logging.WARNING, logger="percepxion_mcp"):
        check_remote_settings(s)
    assert "SECURITY WARNING" in caplog.text


def test_settings_from_env_reads_everything():
    s = settings_from_env(
        "both",
        "0.0.0.0",
        {"MCP_AUTH_TOKEN": TOKEN, "MCP_ALLOWED_HOSTS": "a.example, 192.0.2.1", "MCP_ALLOWED_ORIGINS": "https://a.example"},
    )
    assert s.token == TOKEN and s.allowed_hosts == ["a.example", "192.0.2.1"]
    assert s.remote_bind


def test_main_exits_on_unsafe_remote_settings(monkeypatch):
    import percepxion_mcp.config as cfg

    monkeypatch.setattr(cfg, "MCP_TRANSPORT", "both")
    monkeypatch.setattr(cfg, "MCP_HOST", "0.0.0.0")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN_FILE", raising=False)
    monkeypatch.delenv("MCP_ALLOW_UNAUTHENTICATED", raising=False)
    with pytest.raises(SystemExit):
        server_mod.main()


# --- 3. Host / Origin validation (DNS rebinding) ---------------------------


def test_remote_bind_requires_allowed_hosts():
    with pytest.raises(RemoteConfigError, match="MCP_ALLOWED_HOSTS"):
        check_remote_settings(RemoteSettings("both", "0.0.0.0", TOKEN))


def test_unlisted_host_refused_on_remote_bind():
    app = build_http_app("both", auth_token=TOKEN, allowed_hosts=["mcp.example.com"], validate_every_request=True)
    r = _run(_post(app, {"Authorization": "Bearer " + TOKEN}, host="evil.example", bind=("0.0.0.0", 8765)))
    assert r.status_code in (400, 403, 421)


def test_listed_host_accepted_on_remote_bind():
    app = build_http_app("both", auth_token=TOKEN, allowed_hosts=["mcp.example.com"], validate_every_request=True)
    r = _run(_post(app, {"Authorization": "Bearer " + TOKEN}, host="mcp.example.com:8765", bind=("0.0.0.0", 8765)))
    assert r.status_code == 200


def test_foreign_origin_refused_on_remote_bind():
    app = build_http_app("both", auth_token=TOKEN, allowed_hosts=["mcp.example.com"], validate_every_request=True)
    r = _run(
        _post(app, {"Authorization": "Bearer " + TOKEN, "Origin": "https://evil.example"}, host="mcp.example.com", bind=("0.0.0.0", 8765))
    )
    assert r.status_code in (400, 403, 421)


def test_allowed_origin_accepted_on_remote_bind():
    app = build_http_app(
        "both",
        auth_token=TOKEN,
        allowed_hosts=["mcp.example.com"],
        allowed_origins=["https://console.example.com"],
        validate_every_request=True,
    )
    r = _run(
        _post(
            app, {"Authorization": "Bearer " + TOKEN, "Origin": "https://console.example.com"},
            host="mcp.example.com",
            bind=("0.0.0.0", 8765),
        )
    )
    assert r.status_code == 200


# --- 2. firmware paths on remote transports --------------------------------


def _fw(path: str):
    return update_firmware_by_smart_group(
        firmware_file_path=path, smart_group_ids=["sg"], content_name="n", version="1"
    )


def test_firmware_disabled_on_remote_transport_without_dir(tmp_path):
    f = tmp_path / "image.bin"
    f.write_bytes(b"x")
    with patch.object(server_mod, "REMOTE_TRANSPORT", True), patch.object(server_mod, "FIRMWARE_DIR", None):
        r = _fw(str(f))
    assert r.get("success") is False or "error" in str(r).lower()
    assert "PERCEPXION_FIRMWARE_DIR" in str(r)


def test_firmware_symlink_out_of_dir_refused(tmp_path):
    safe = tmp_path / "fw"
    safe.mkdir()
    secret = tmp_path / "secret.env"
    secret.write_text("PASSWORD=x")
    link = safe / "image.bin"
    os.symlink(secret, link)
    with patch.object(server_mod, "REMOTE_TRANSPORT", True), patch.object(server_mod, "FIRMWARE_DIR", str(safe)):
        r = _fw(str(link))
    assert "outside the allowed directory" in str(r)


def test_firmware_outside_dir_reveals_nothing(tmp_path):
    safe = tmp_path / "fw"
    safe.mkdir()
    with patch.object(server_mod, "REMOTE_TRANSPORT", True), patch.object(server_mod, "FIRMWARE_DIR", str(safe)):
        r = _fw("/definitely/not/here.bin")
    assert "outside the allowed directory" in str(r)
    assert "not found" not in str(r)
