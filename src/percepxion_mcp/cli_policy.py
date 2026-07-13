import os
import re
from typing import Final

DEFAULT_DENY_COMMANDS: Final[frozenset[str]] = frozenset({
    "factory-reset",
    "write erase",
    "erase startup-config",
    "erase flash",
    "reload",
    "reboot",
    "format",
    "shutdown",
    "power off",
    "reset system",
    "init 0",
    "halt",
})

_READ_PREFIXES: Final[tuple[str, ...]] = (
    "show",
    "get",
    "list",
    "display",
    "status",
    "ping",
    "traceroute",
    "whois",
    "version",
    "help",
    "?",
    "dir",
    "more",
    "type",
)

# Module-level defaults (loaded from env at import time; overridable per-call in tests)
_CLI_WRITE_ENABLED: bool = os.getenv("PERCEPXION_CLI_WRITE_ENABLED", "false").lower() == "true"
_CLI_YOLO_MODE: bool = os.getenv("PERCEPXION_CLI_YOLO", "false").lower() == "true"
_CLI_MAX_LENGTH: int = int(os.getenv("PERCEPXION_CLI_MAX_LENGTH", "512"))


def _load_env_set(env_var: str) -> frozenset[str]:
    raw = os.getenv(env_var, "")
    if not raw.strip():
        return frozenset()
    return frozenset(c.strip().lower() for c in raw.split(",") if c.strip())


_EXTRA_DENY_FROM_ENV: frozenset[str] = _load_env_set("PERCEPXION_CLI_DENY_COMMANDS")
_PERMIT_FROM_ENV: frozenset[str] = _load_env_set("PERCEPXION_CLI_PERMIT_COMMANDS")


class CLIPolicyViolation(Exception):
    """Raised when a command is blocked by policy."""


def _normalize(command: str) -> str:
    return re.sub(r"\s+", " ", command.strip().lower())


def _is_read_command(command: str) -> bool:
    normalized = _normalize(command)
    return any(
        normalized == p or normalized.startswith(p + " ")
        for p in _READ_PREFIXES
    )


def check_command(
    command: str,
    write_enabled: bool | None = None,
    yolo_mode: bool | None = None,
    max_length: int | None = None,
    extra_deny: frozenset[str] | None = None,
    permit_list: frozenset[str] | None = None,
) -> None:
    """
    Validate a CLI command against the current policy.
    Raises CLIPolicyViolation if the command is not allowed.

    Keyword args override module-level env-var defaults (useful for testing).
    """
    _write = _CLI_WRITE_ENABLED if write_enabled is None else write_enabled
    _yolo = _CLI_YOLO_MODE if yolo_mode is None else yolo_mode
    _max = _CLI_MAX_LENGTH if max_length is None else max_length
    _extra = _EXTRA_DENY_FROM_ENV if extra_deny is None else extra_deny
    _permit = _PERMIT_FROM_ENV if permit_list is None else permit_list

    deny = DEFAULT_DENY_COMMANDS | _extra

    if not command or not command.strip():
        raise CLIPolicyViolation("Command cannot be empty.")

    if re.search(r"[\r\n]", command):
        raise CLIPolicyViolation(
            "Command contains embedded newline/carriage-return characters, which is not allowed. "
            "Submit one command per call."
        )

    if len(command) > _max:
        raise CLIPolicyViolation(
            f"Command exceeds maximum length ({_max} chars). Got {len(command)}."
        )

    if _yolo:
        return  # no further checks

    normalized = _normalize(command)

    for denied in deny:
        if normalized == denied or normalized.startswith(denied + " "):
            raise CLIPolicyViolation(
                f"Command '{command}' is in the deny list. "
                "Set PERCEPXION_CLI_YOLO=true to bypass (use with caution)."
            )

    if not _write and not _is_read_command(command):
        raise CLIPolicyViolation(
            f"Write commands are disabled. Set PERCEPXION_CLI_WRITE_ENABLED=true to allow. "
            f"Command '{command}' is not a recognized read-only command (show/get/ping/etc.)."
        )

    if _permit:
        permit_normalized = {p.strip().lower() for p in _permit}
        if not any(
            normalized == p or normalized.startswith(p + " ")
            for p in permit_normalized
        ):
            raise CLIPolicyViolation(
                f"Command '{command}' is not in the permit list. "
                "Update PERCEPXION_CLI_PERMIT_COMMANDS or clear it to allow all non-denied commands."
            )
