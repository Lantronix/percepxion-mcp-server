"""Read and write tool classes, read-only mode and the MCP_WRITES_UNTIL window.

Most tests register the server's tools on a fresh FastMCP instance with an
explicit policy, so no test depends on the environment the suite runs in. One
test starts a subprocess to check the real MCP_READ_ONLY wiring in server.py.
No test calls the Percepxion platform.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.messages import MessageHandler
from fastmcp.exceptions import ToolError

from percepxion_mcp import cli_policy, server
from percepxion_mcp.tool_access import AccessPolicy, policy_from_env

REPO_ROOT = Path(__file__).resolve().parents[1]

# The 38 tools of the 2.2.0 release. With no MCP_READ_ONLY the list must not change.
EXPECTED_TOOLS = {
    "login_with_env", "reconfigure_credentials", "get_device_list", "get_device_details",
    "get_devices_by_organization", "list_organizations", "list_tenants",
    "import_and_assign_devices", "unassign_devices", "remove_device_from_platform",
    "create_smart_group", "list_smart_groups", "delete_smart_group", "send_direct_cli_command",
    "get_cli_command_output", "get_job_results_by_device", "update_device_config",
    "clone_device_config", "get_device_firmware_status", "reboot_device", "get_device_config",
    "request_device_syslog_upload", "get_device_syslogs", "get_security_telemetry",
    "investigate_audit_logs", "investigate_user_audit_logs", "set_user_access",
    "update_firmware_by_smart_group", "list_firmware_content", "list_templates",
    "delete_template", "search_job_groups", "get_job_group", "query_device_access_log",
    "download_device_access_log", "list_device_ports", "get_port_telemetry",
    "firmware_compliance_report",
}

# Pinned so a change of class is a deliberate, reviewed edit.
EXPECTED_WRITE_TOOLS = {
    "reconfigure_credentials", "import_and_assign_devices", "unassign_devices",
    "remove_device_from_platform", "create_smart_group", "delete_smart_group",
    "send_direct_cli_command", "update_device_config", "clone_device_config", "reboot_device",
    "request_device_syslog_upload", "set_user_access", "update_firmware_by_smart_group",
    "delete_template",
}


class FakeClock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


class ListChangedRecorder(MessageHandler):
    def __init__(self):
        super().__init__()
        self.count = 0
        self.event = asyncio.Event()

    async def on_tool_list_changed(self, message) -> None:
        self.count += 1
        self.event.set()


@pytest.fixture(autouse=True)
def restore_cli_flags(monkeypatch):
    """Read-only registration forces the CLI flags off; put them back after each test."""
    monkeypatch.setattr(cli_policy, "_CLI_WRITE_ENABLED", cli_policy._CLI_WRITE_ENABLED)
    monkeypatch.setattr(cli_policy, "_CLI_YOLO_MODE", cli_policy._CLI_YOLO_MODE)


def build(policy: AccessPolicy):
    """The server's tools on a fresh FastMCP instance under `policy`."""
    fresh = FastMCP(server.SERVER_NAME)
    guard = server.tools.register(fresh, policy)
    return fresh, guard


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=30))


async def tool_names(srv: FastMCP) -> set[str]:
    async with Client(srv) as c:
        return {t.name for t in await c.list_tools()}


# --- every tool declares its class ---


def test_every_registered_tool_declares_read_only_hint():
    tools = run(server.mcp.list_tools())
    missing = [t.name for t in tools if t.annotations is None or t.annotations.readOnlyHint is None]
    assert not missing, (
        f"Tools without readOnlyHint: {missing}. Register each tool with @read_tool or "
        "@write_tool (see tool_access.py), not @mcp.tool()."
    )
    for t in tools:
        if t.annotations.readOnlyHint is False:
            assert t.annotations.destructiveHint is True, f"{t.name} is a write without destructiveHint"


def test_every_registered_tool_is_in_the_registry():
    registered = {t.name for t in run(server.mcp.list_tools())}
    assert registered == server.tools.read_names | server.tools.write_names


def test_write_classification_is_pinned():
    assert server.tools.write_names == EXPECTED_WRITE_TOOLS
    assert server.tools.read_names == EXPECTED_TOOLS - EXPECTED_WRITE_TOOLS


def test_default_tool_list_is_unchanged():
    names = [t.name for t in run(server.mcp.list_tools())]
    assert len(names) == 38
    assert set(names) == EXPECTED_TOOLS


def test_annotations_reach_the_client():
    srv, _ = build(AccessPolicy())

    async def go():
        async with Client(srv) as c:
            return {t.name: t.annotations for t in await c.list_tools()}

    annotations = run(go())
    assert annotations["get_device_list"].readOnlyHint is True
    assert annotations["reboot_device"].readOnlyHint is False
    assert annotations["reboot_device"].destructiveHint is True


# --- MCP_READ_ONLY ---


def test_read_only_registers_no_write_tool():
    srv, _ = build(AccessPolicy(read_only=True))
    names = run(tool_names(srv))
    assert names == EXPECTED_TOOLS - EXPECTED_WRITE_TOOLS
    assert "login_with_env" in names


@pytest.mark.parametrize("name", sorted(EXPECTED_WRITE_TOOLS))
def test_read_only_refuses_a_write_call_by_name(name):
    srv, _ = build(AccessPolicy(read_only=True))

    async def go():
        async with Client(srv) as c:
            await c.call_tool(name, {})

    with pytest.raises(ToolError, match="read-only"):
        run(go())


def test_read_only_still_runs_read_tools():
    srv, _ = build(AccessPolicy(read_only=True))

    async def go():
        async with Client(srv) as c:
            return await c.call_tool("get_device_details", {}, raise_on_error=False)

    result = run(go())
    # Refused by the tool's own argument check, not by the read-only guard.
    assert "read-only" not in json.dumps(result.structured_content)
    assert result.structured_content["ok"] is False


def test_unknown_tool_is_not_reported_as_read_only():
    srv, _ = build(AccessPolicy(read_only=True))

    async def go():
        async with Client(srv) as c:
            await c.call_tool("no_such_tool", {})

    with pytest.raises(ToolError) as exc:
        run(go())
    assert "read-only" not in str(exc.value)


def test_read_only_forces_cli_writes_off(monkeypatch):
    monkeypatch.setattr(cli_policy, "_CLI_WRITE_ENABLED", True)
    monkeypatch.setattr(cli_policy, "_CLI_YOLO_MODE", True)
    build(AccessPolicy(read_only=True))
    assert cli_policy._CLI_WRITE_ENABLED is False
    assert cli_policy._CLI_YOLO_MODE is False
    with pytest.raises(cli_policy.CLIPolicyViolation, match="Write commands are disabled"):
        cli_policy.check_command("configure terminal")


def test_default_policy_leaves_cli_flags_alone(monkeypatch):
    monkeypatch.setattr(cli_policy, "_CLI_WRITE_ENABLED", True)
    build(AccessPolicy())
    assert cli_policy._CLI_WRITE_ENABLED is True


def test_read_only_env_wiring_in_a_fresh_process(tmp_path):
    script = (
        "import asyncio, json\n"
        "from percepxion_mcp import server, cli_policy\n"
        "names = [t.name for t in asyncio.run(server.mcp.list_tools())]\n"
        "print(json.dumps({'names': names, 'cli_write': cli_policy._CLI_WRITE_ENABLED}))\n"
    )
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("MCP_", "PERCEPXION_"))
    }
    env.update({
        "MCP_READ_ONLY": "true",
        "PERCEPXION_CLI_WRITE_ENABLED": "true",
        "PYTHONPATH": str(REPO_ROOT / "src"),
    })
    out = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=60, check=True,
    )
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert set(data["names"]) == EXPECTED_TOOLS - EXPECTED_WRITE_TOOLS
    assert data["cli_write"] is False


# --- environment parsing ---


@pytest.mark.parametrize(
    "env, read_only, until",
    [
        ({}, False, None),
        ({"MCP_READ_ONLY": "false"}, False, None),
        ({"MCP_READ_ONLY": "TRUE"}, True, None),
        ({"MCP_READ_ONLY": "1"}, True, None),
        ({"MCP_WRITES_UNTIL": "2000000000"}, False, 2000000000.0),
        # MCP_WRITES_UNTIL is ignored when MCP_READ_ONLY is true.
        ({"MCP_READ_ONLY": "true", "MCP_WRITES_UNTIL": "2000000000"}, True, None),
        # Values that can't be parsed fail closed.
        ({"MCP_READ_ONLY": "maybe"}, True, None),
        ({"MCP_WRITES_UNTIL": "tomorrow"}, True, None),
    ],
)
def test_policy_from_env(env, read_only, until):
    policy = policy_from_env(env)
    assert policy.read_only is read_only
    assert policy.writes_until == until


# --- MCP_WRITES_UNTIL ---


def test_writes_until_in_the_past_starts_read_only(monkeypatch):
    monkeypatch.setattr(cli_policy, "_CLI_WRITE_ENABLED", True)
    srv, guard = build(AccessPolicy(writes_until=1000.0, clock=FakeClock(1000.0)))
    assert run(tool_names(srv)) == EXPECTED_TOOLS - EXPECTED_WRITE_TOOLS
    assert guard.expired is True
    assert cli_policy._CLI_WRITE_ENABLED is False


def test_writes_until_expiry_disables_writes_and_notifies(monkeypatch):
    monkeypatch.setattr(cli_policy, "_CLI_WRITE_ENABLED", True)
    clock = FakeClock(1000.0)
    srv, guard = build(AccessPolicy(writes_until=2000.0, clock=clock))
    recorder = ListChangedRecorder()

    async def go():
        async with Client(srv, message_handler=recorder) as c:
            before = {t.name for t in await c.list_tools()}
            clock.now = 2000.0
            after = {t.name for t in await c.list_tools()}
            await asyncio.wait_for(recorder.event.wait(), timeout=5)
            return before, after

    before, after = run(go())
    assert before == EXPECTED_TOOLS
    assert after == EXPECTED_TOOLS - EXPECTED_WRITE_TOOLS
    assert recorder.count == 1
    assert guard.expired is True
    assert cli_policy._CLI_WRITE_ENABLED is False


def test_write_call_after_expiry_is_refused_from_a_cached_list():
    clock = FakeClock(1000.0)
    srv, _ = build(AccessPolicy(writes_until=2000.0, clock=clock))

    async def go():
        async with Client(srv) as c:
            cached = {t.name for t in await c.list_tools()}
            assert "reboot_device" in cached
            clock.now = 2500.0
            # No new tools/list: the client still believes reboot_device exists.
            await c.call_tool("reboot_device", {"device_id": "d1"})

    with pytest.raises(ToolError, match="read-only"):
        run(go())


def test_write_call_inside_the_window_is_not_refused():
    srv, _ = build(AccessPolicy(writes_until=2000.0, clock=FakeClock(1000.0)))

    async def go():
        async with Client(srv) as c:
            # Fails inside the tool (bad provider name) with no platform call.
            return await c.call_tool("reconfigure_credentials", {"provider": "nope"}, raise_on_error=False)

    result = run(go())
    assert "read-only" not in json.dumps(result.structured_content)
    assert "Invalid provider" in result.structured_content["error"]


def test_writes_until_timer_fires_without_a_request():
    srv, guard = build(AccessPolicy(writes_until=time.time() + 0.5))
    recorder = ListChangedRecorder()

    async def go():
        async with Client(srv, message_handler=recorder) as c:
            assert "reboot_device" in {t.name for t in await c.list_tools()}
            await asyncio.wait_for(recorder.event.wait(), timeout=10)
            assert guard.expired is True
            return {t.name for t in await c.list_tools()}

    assert run(go()) == EXPECTED_TOOLS - EXPECTED_WRITE_TOOLS


# --- reconfigure_credentials on stdio ---


def test_reconfigure_credentials_works_in_process(monkeypatch):
    monkeypatch.setenv("PERCEPXION_CREDENTIAL_PROVIDER", "env")
    srv, _ = build(AccessPolicy())

    async def go():
        async with Client(srv) as c:
            return await c.call_tool("reconfigure_credentials", {"provider": "env"}, raise_on_error=False)

    result = run(go())
    assert result.structured_content["ok"] is True
