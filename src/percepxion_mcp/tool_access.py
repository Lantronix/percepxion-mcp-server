"""Read and write tool classes, read-only mode and the timed write window.

Every tool is registered through a ToolRegistry as either a read tool or a
write tool. The class is published to clients as the MCP standard annotations:
read tools carry readOnlyHint=true; write tools carry readOnlyHint=false and
destructiveHint=true. A tool counts as a write when it changes state on the
Percepxion platform, on a device, or in this server's own credential setup.

Two environment variables control which tools a server offers:

- MCP_READ_ONLY (default false). When true, write tools are not registered, a
  call to one by name is refused with a "read-only" error, and the CLI write
  and YOLO flags are forced off whatever their environment values.
- MCP_WRITES_UNTIL (epoch seconds), honored only when MCP_READ_ONLY is false.
  A time already past at start-up means read-only. A future time registers the
  write tools, then disables them at that time and sends tools/list_changed to
  connected clients. A write call after that time is refused even from a
  client holding an older tool list.

Unparseable values fail closed: the server starts read-only and logs why.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import weakref
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
from fastmcp.server.providers import Provider
from fastmcp.tools import Tool
from mcp.types import ToolAnnotations

from . import cli_policy

logger = logging.getLogger("percepxion_mcp")

READ_ANNOTATIONS = ToolAnnotations(readOnlyHint=True)
WRITE_ANNOTATIONS = ToolAnnotations(readOnlyHint=False, destructiveHint=True)

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("", "0", "false", "no", "off")

# Longest single sleep in the expiry timer, so a suspended host or a clock
# change is noticed within this many seconds.
_TIMER_MAX_SLEEP = 30.0


class ConfigError(ValueError):
    """An access setting could not be parsed."""


@dataclass
class AccessPolicy:
    """Whether write tools are allowed now.

    clock returns epoch seconds; tests pass their own.
    """

    read_only: bool = False
    writes_until: float | None = None
    clock: Callable[[], float] = time.time
    reason: str = ""

    def writes_allowed(self) -> bool:
        if self.read_only:
            return False
        if self.writes_until is None:
            return True
        return self.clock() < self.writes_until

    def describe(self) -> str:
        if self.read_only:
            return self.reason or "MCP_READ_ONLY=true"
        if self.writes_until is not None and not self.writes_allowed():
            ended = datetime.fromtimestamp(self.writes_until, tz=UTC)
            return f"the write window set by MCP_WRITES_UNTIL ended at {ended.isoformat()}"
        return "writes are allowed"


def _parse_bool(name: str, raw: str | None) -> bool:
    value = (raw or "").strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{name}={raw!r} is not true or false")


def _parse_epoch(name: str, raw: str | None) -> float | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        raise ConfigError(f"{name}={raw!r} is not a number of epoch seconds") from None


def policy_from_env(
    env: dict[str, str] | None = None,
    clock: Callable[[], float] = time.time,
) -> AccessPolicy:
    """Build the policy from MCP_READ_ONLY and MCP_WRITES_UNTIL.

    A value that can't be parsed gives a read-only policy, never a writable one.
    """
    env = os.environ if env is None else env
    try:
        read_only = _parse_bool("MCP_READ_ONLY", env.get("MCP_READ_ONLY"))
        writes_until = None if read_only else _parse_epoch("MCP_WRITES_UNTIL", env.get("MCP_WRITES_UNTIL"))
    except ConfigError as e:
        logger.error("%s; starting read-only.", e)
        return AccessPolicy(read_only=True, clock=clock, reason=f"{e}, so the server is read-only")
    return AccessPolicy(read_only=read_only, writes_until=writes_until, clock=clock)


def _force_cli_read_only() -> None:
    cli_policy.force_read_only()


class _WriteGuard(Middleware):
    """Refuses write calls when writes are off and disables write tools at expiry."""

    def __init__(self, server: FastMCP, policy: AccessPolicy, read_names: set[str], write_names: set[str]):
        self.server = server
        self.policy = policy
        self.read_names = read_names
        self.write_names = write_names
        self.expired = False
        self._sessions: weakref.WeakSet[Any] = weakref.WeakSet()
        self._lock = asyncio.Lock()

    def _track_session(self, context) -> None:
        ctx = context.fastmcp_context
        if ctx is None:
            return
        try:
            self._sessions.add(ctx.session)
        except (RuntimeError, TypeError):
            pass

    async def expire(self) -> None:
        """Disable the write tools and tell connected clients. Safe to call twice."""
        async with self._lock:
            if self.expired:
                return
            self.expired = True
            _force_cli_read_only()
            if self.write_names:
                self.server.disable(names=set(self.write_names), components={"tool"})
            logger.warning(
                "Write tools disabled: %s. The server is read-only until it restarts with new settings.",
                self.policy.describe(),
            )
            sessions = list(self._sessions)
        for session in sessions:
            try:
                await session.send_tool_list_changed()
            except Exception as e:  # a closed session must not stop the others
                logger.debug("tools/list_changed not delivered to one session: %s", e)

    async def expire_if_due(self) -> None:
        if not self.expired and not self.policy.writes_allowed():
            await self.expire()

    async def on_request(self, context, call_next):
        self._track_session(context)
        return await call_next(context)

    async def on_list_tools(self, context, call_next):
        await self.expire_if_due()
        return await call_next(context)

    async def on_call_tool(self, context, call_next):
        name = context.message.name
        if await self._is_write(name) and not self.policy.writes_allowed():
            await self.expire_if_due()
            raise ToolError(f"{name} is a write tool and this server is read-only ({self.policy.describe()}).")
        return await call_next(context)

    async def _is_write(self, name: str) -> bool:
        if name in self.write_names:
            return True
        if name in self.read_names:
            return False
        # A tool registered without a class: treat it as a write.
        return await self.server.get_tool(name) is not None


class _ExpiryTimer(Provider):
    """Holds no components; its lifespan runs the write-window timer."""

    def __init__(self, guard: _WriteGuard):
        super().__init__()
        self.guard = guard

    async def _run(self) -> None:
        policy = self.guard.policy
        while not self.guard.expired:
            remaining = policy.writes_until - policy.clock()
            if remaining <= 0:
                await self.guard.expire()
                return
            await asyncio.sleep(min(remaining, _TIMER_MAX_SLEEP))

    @asynccontextmanager
    async def lifespan(self):
        task = asyncio.create_task(self._run())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Write-window timer failed")


@dataclass
class ToolRegistry:
    """Records each tool with its class, then registers them on a server."""

    _tools: list[tuple[Callable[..., Any], bool]] = field(default_factory=list)

    def read(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Decorator: a tool that only reads."""
        self._tools.append((fn, True))
        return fn

    def write(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Decorator: a tool that changes platform, device or server state."""
        self._tools.append((fn, False))
        return fn

    @property
    def read_names(self) -> set[str]:
        return {fn.__name__ for fn, is_read in self._tools if is_read}

    @property
    def write_names(self) -> set[str]:
        return {fn.__name__ for fn, is_read in self._tools if not is_read}

    def register(self, server: FastMCP, policy: AccessPolicy) -> _WriteGuard:
        """Register the tools the policy allows and install the write guard."""
        writes_now = policy.writes_allowed()
        for fn, is_read in self._tools:
            if not is_read and not writes_now:
                continue
            annotations = READ_ANNOTATIONS if is_read else WRITE_ANNOTATIONS
            server.add_tool(Tool.from_function(fn, annotations=annotations))

        guard = _WriteGuard(server, policy, self.read_names, self.write_names)
        server.add_middleware(guard)
        if not writes_now:
            guard.expired = True
            _force_cli_read_only()
            logger.info("Read-only: %d write tools not registered (%s).", len(self.write_names), policy.describe())
        elif policy.writes_until is not None:
            server.add_provider(_ExpiryTimer(guard))
            logger.info(
                "Write tools enabled until %s (MCP_WRITES_UNTIL).",
                datetime.fromtimestamp(policy.writes_until, tz=UTC).isoformat(),
            )
        return guard
