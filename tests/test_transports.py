"""Remote transports: Streamable HTTP at /mcp and legacy SSE at /sse.

Each test starts the real ASGI app under uvicorn on a free local port and
connects with the MCP SDK client, so it exercises the same path a remote MCP
client uses. Only initialize and tools/list run; no Percepxion call is made.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time

import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from percepxion_mcp.server import build_http_app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server:
    def __init__(self, transport: str, **kwargs):
        self.port = _free_port()
        app = build_http_app(transport, **kwargs)
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning", lifespan="on")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("uvicorn did not start")
            time.sleep(0.05)
        return f"http://127.0.0.1:{self.port}"

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)


async def _list_over_http(base: str) -> list[str]:
    async with streamable_http_client(f"{base}/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            assert init.serverInfo.name == "Percepxion-Server"
            return [t.name for t in (await session.list_tools()).tools]


async def _list_over_sse(base: str) -> list[str]:
    async with sse_client(f"{base}/sse") as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            assert init.serverInfo.name == "Percepxion-Server"
            return [t.name for t in (await session.list_tools()).tools]


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=30))


def test_both_serves_streamable_http_and_sse():
    with _Server("both") as base:
        http_tools = _run(_list_over_http(base))
        sse_tools = _run(_list_over_sse(base))
    assert "login_with_env" in http_tools
    assert sorted(http_tools) == sorted(sse_tools)


def test_http_only_has_no_sse_route():
    with _Server("http") as base:
        assert "login_with_env" in _run(_list_over_http(base))
        with pytest.raises(BaseException):
            _run(_list_over_sse(base))


def test_sse_only_keeps_the_legacy_paths():
    with _Server("sse") as base:
        assert "login_with_env" in _run(_list_over_sse(base))
        with pytest.raises(BaseException):
            _run(_list_over_http(base))


def test_stateless_http_still_answers():
    with _Server("http", stateless_http=True) as base:
        assert "login_with_env" in _run(_list_over_http(base))


def test_unknown_transport_is_refused():
    with pytest.raises(ValueError, match="MCP_TRANSPORT"):
        build_http_app("websocket")


async def _reconfigure(open_streams) -> dict:
    async with open_streams as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("reconfigure_credentials", {"provider": "env"})
            return json.loads(result.content[0].text)


@pytest.mark.parametrize("transport", ["http", "sse", "both"])
def test_reconfigure_credentials_is_refused_over_http(monkeypatch, transport):
    monkeypatch.setenv("PERCEPXION_CREDENTIAL_PROVIDER", "vault")
    with _Server(transport) as base:
        if transport == "sse":
            data = _run(_reconfigure(sse_client(f"{base}/sse")))
        else:
            data = _run(_reconfigure(streamable_http_client(f"{base}/mcp")))
    assert data["ok"] is False
    assert "stdio only" in data["error"]
    # The provider the server was started with is untouched.
    assert os.environ["PERCEPXION_CREDENTIAL_PROVIDER"] == "vault"


def test_reconfigure_credentials_is_still_listed_over_http():
    with _Server("http") as base:
        assert "reconfigure_credentials" in _run(_list_over_http(base))
