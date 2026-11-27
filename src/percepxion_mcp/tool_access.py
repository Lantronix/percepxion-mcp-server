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
- MCP_WRITES_UNTIL (whole epoch seconds), honored only when MCP_READ_ONLY is
  false. A time already past at start-up means read-only. A future time, at
  most 7 days ahead, registers the write tools, then disables them at that
  time and sends tools/list_changed to connected clients. A write call after
  that time is refused even from a client holding an older tool list. The
  deadline is also kept on the monotonic clock, and whichever clock reaches it
  first ends the window, so setting the wall clock back doesn't extend it.

Invalid values fail closed: the server starts read-only and logs why. That
includes anything that isn't a whole number, a value above 1e10 (milliseconds
by mistake) and a time more than 7 days after start-up.
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import os
import re
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

# MCP_WRITES_UNTIL limits. Epoch seconds pass 1e10 in the year 2286, so a larger
# value is milliseconds by mistake.
_MAX_EPOCH = 10_000_000_000
MAX_WRITE_WINDOW = 7 * 24 * 3600
_INTEGER = re.compile(r"^\+?[0-9]+$")


class ConfigError(ValueError):
    """An access setting could not be parsed."""


def _iso(epoch: float) -> str:
    try:
        return datetime.fromtimestamp(epoch, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return f"epoch {epoch!r}"


@dataclass
class AccessPolicy:
    """Whether write tools are allowed now.

    clock returns epoch seconds and monotonic a monotonic reading; tests pass
    their own. The write window ends at writes_until on the wall clock or at the
    same distance from start-up on the monotonic clock, whichever comes first.
    """

    read_only: bool = False
    writes_until: float | None = None
    clock: Callable[[], float] = time.time
    reason: str = ""
    monotonic: Callable[[], float] = time.monotonic
    monotonic_deadline: float | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.writes_until is not None:
            self.monotonic_deadline = self.monotonic() + (self.writes_until - self.clock())

    def seconds_left(self) -> float:
        """Seconds until the window ends by either clock (inf with no window)."""
        if self.writes_until is None:
            return float("inf")
        return min(self.writes_until - self.clock(), self.monotonic_deadline - self.monotonic())

    def writes_allowed(self) -> bool:
        if self.read_only:
            return False
        return self.seconds_left() > 0

    def describe(self) -> str:
        if self.read_only:
            return self.reason or "MCP_READ_ONLY=true"
        if self.writes_until is not None and not self.writes_allowed():
            return f"the write window set by MCP_WRITES_UNTIL ended (deadline {_iso(self.writes_until)})"
        return "writes are allowed"


def _parse_bool(name: str, raw: str | None) -> bool:
    value = (raw or "").strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{name}={raw!r} is not true or false")


def _parse_epoch(name: str, raw: str | None, now: float) -> float | None:
    """Whole epoch seconds, at most 1e10 and at most MAX_WRITE_WINDOW after now."""
    value = (raw or "").strip()
    if not value:
        return None
    if not _INTEGER.match(value):
        raise ConfigError(f"{name}={raw!r} is not a whole number of epoch seconds")
    epoch = int(value)
    if epoch > _MAX_EPOCH:
        raise ConfigError(f"{name}={raw!r} is above 1e10 (milliseconds instead of seconds?)")
    if epoch - now > MAX_WRITE_WINDOW:
        raise ConfigError(f"{name}={raw!r} is more than 7 days after start-up")
    return float(epoch)


def policy_from_env(
    env: dict[str, str] | None = None,
    clock: Callable[[], float] = time.time,
    monotonic: Callable[[], float] = time.monotonic,
) -> AccessPolicy:
    """Build the policy from MCP_READ_ONLY and MCP_WRITES_UNTIL.

    A value that can't be parsed gives a read-only policy, never a writable one.
    """
    env = os.environ if env is None else env
    try:
        read_only = _parse_bool("MCP_READ_ONLY", env.get("MCP_READ_ONLY"))
        writes_until = (
            None if read_only else _parse_epoch("MCP_WRITES_UNTIL", env.get("MCP_WRITES_UNTIL"), clock())
        )
    except ConfigError as e:
        logger.error("%s; starting read-only.", e)
        return AccessPolicy(
            read_only=True, clock=clock, monotonic=monotonic, reason=f"{e}, so the server is read-only"
        )
    return AccessPolicy(read_only=read_only, writes_until=writes_until, clock=clock, monotonic=monotonic)


# The guard of the server running the current write tool, set by the wrapper
# ToolRegistry puts around each write function.
_active_guard: contextvars.ContextVar[_WriteGuard | None] = contextvars.ContextVar(
    "percepxion_active_write_guard", default=None
)


def write_refusal() -> str | None:
    """Why a write must not happen now, or None when it may.

    Multi-step write tools call this before each platform write, so a write
    window that ends mid-call stops the remaining steps. Outside a registered
    tool call (a direct Python call) there is no guard and writes may proceed.
    """
    guard = _active_guard.get()
    if guard is None or guard.writes_open():
        return None
    return guard.refusal_text()


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

    def writes_open(self) -> bool:
        """The single answer every check uses: writes allowed and not yet expired."""
        if self.expired:
            return False
        return self.policy.writes_allowed()

    def refusal_text(self, name: str | None = None) -> str:
        what = f"{name} is a write tool and this" if name else "This"
        return f"{what} server is read-only ({self.policy.describe()})."

    async def expire_if_due(self) -> None:
        if not self.writes_open():
            await self.expire()

    async def on_request(self, context, call_next):
        self._track_session(context)
        return await call_next(context)

    async def on_list_tools(self, context, call_next):
        await self.expire_if_due()
        return await call_next(context)

    async def on_call_tool(self, context, call_next):
        name = context.message.name
        await self.expire_if_due()
        # With writes off, only a name known to be a read tool passes. That
        # covers write tools, tools registered without a class, and any other
        # address form FastMCP resolves (such as hashed backend names).
        if self.expired and name not in self.read_names:
            raise ToolError(self.refusal_text(name))
        return await call_next(context)

    def wrap(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """A write function that checks the guard itself before running."""

        @functools.wraps(fn)
        def guarded(*args, **kwargs):
            if not self.writes_open():
                raise ToolError(self.refusal_text(fn.__name__))
            token = _active_guard.set(self)
            try:
                return fn(*args, **kwargs)
            finally:
                _active_guard.reset(token)

        return guarded


class _ExpiryTimer(Provider):
    """Holds no components; its lifespan runs the write-window timer."""

    def __init__(self, guard: _WriteGuard):
        super().__init__()
        self.guard = guard

    async def _run(self) -> None:
        policy = self.guard.policy
        while not self.guard.expired:
            remaining = policy.seconds_left()
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
        guard = _WriteGuard(server, policy, self.read_names, self.write_names)
        for fn, is_read in self._tools:
            if is_read:
                server.add_tool(Tool.from_function(fn, annotations=READ_ANNOTATIONS))
            elif writes_now:
                server.add_tool(Tool.from_function(guard.wrap(fn), annotations=WRITE_ANNOTATIONS))

        server.add_middleware(guard)
        if not writes_now:
            guard.expired = True
            _force_cli_read_only()
            logger.info("Read-only: %d write tools not registered (%s).", len(self.write_names), policy.describe())
        elif policy.writes_until is not None:
            server.add_provider(_ExpiryTimer(guard))
            logger.info("Write tools enabled until %s (MCP_WRITES_UNTIL).", _iso(policy.writes_until))
        return guard
