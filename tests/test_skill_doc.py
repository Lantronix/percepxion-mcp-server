"""Guard against drift between skills/percepxion-fleet-ops/SKILL.md and server.py.

Two checks:
1. Every tool-shaped name the skill mentions exists, either as a registered tool
   here or as a documented companion tool on slc-mcp-server.
2. Every registered tool is mentioned in the skill at least once, so new tools
   don't ship undocumented.

Tool names in both repos are verb-first by convention, which is what lets a
regex separate tool mentions from parameter names.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_PY = REPO_ROOT / "src" / "percepxion_mcp" / "server.py"
SKILL_MD = REPO_ROOT / "skills" / "percepxion-fleet-ops" / "SKILL.md"

# Tools on the companion slc-mcp-server that the skill legitimately mentions
# (capability split table and CLI routing guidance).
COMPANION_TOOLS = {
    "get_slc_port",
    "get_slc_ports",
    "apply_config_commands",
    "firmware_update",
    "get_firmware_update_status",
    "export_config_commands",
    "get_sessions",
    "terminate_session",
    "get_cellular_status",
}

# Backticked tokens that match the verb-first pattern but are parameters, not tools.
NON_TOOL_TOKENS = {
    "search_query",
    "search_string",
}

# Verb-first prefixes used by tool names across both servers.
TOOL_NAME_RE = re.compile(
    r"^(get|list|send|create|delete|update|import|reboot|remove|unassign|request"
    r"|search|investigate|download|query|clone|firmware|login|reconfigure|apply"
    r"|export|save|restore|check|set|start|stop|restart|terminate|logout"
    r"|configure|factory)_[a-z0-9_]+$"
)


def registered_tools() -> set[str]:
    src = SERVER_PY.read_text()
    return set(re.findall(r"@mcp\.tool\(\)\s*\ndef (\w+)", src))


def skill_tool_mentions() -> set[str]:
    text = SKILL_MD.read_text()
    tokens = set(re.findall(r"`([a-z][a-z0-9_]*)(?:\(|`)", text))
    return {
        t for t in tokens if TOOL_NAME_RE.match(t) and t not in NON_TOOL_TOKENS
    }


def test_skill_mentions_only_real_tools():
    tools = registered_tools()
    mentioned = skill_tool_mentions()
    phantoms = mentioned - tools - COMPANION_TOOLS
    assert not phantoms, (
        f"SKILL.md mentions tools that don't exist in server.py or the "
        f"companion allowlist: {sorted(phantoms)}. Either the tool was renamed/"
        f"removed (update the skill) or a parameter slipped past the filter "
        f"(add it to NON_TOOL_TOKENS)."
    )


def test_every_tool_is_documented_in_skill():
    tools = registered_tools()
    mentioned = skill_tool_mentions()
    missing = tools - mentioned
    assert not missing, (
        f"Registered tools not mentioned anywhere in SKILL.md: {sorted(missing)}. "
        f"New tools need at least a one-line mention in the relevant workflow "
        f"so agent users learn they exist."
    )
