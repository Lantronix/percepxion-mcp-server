import pytest
from percepxion_mcp.cli_policy import check_command, CLIPolicyViolation, DEFAULT_DENY_COMMANDS


# --- length ---

def test_empty_command_raises():
    with pytest.raises(CLIPolicyViolation, match="empty"):
        check_command("")


def test_whitespace_only_raises():
    with pytest.raises(CLIPolicyViolation, match="empty"):
        check_command("   ")


def test_command_exceeding_max_length_raises():
    long_cmd = "show " + "x" * 600
    with pytest.raises(CLIPolicyViolation, match="maximum length"):
        check_command(long_cmd, max_length=512)


def test_command_at_max_length_is_allowed():
    cmd = "show " + "x" * 507  # exactly 512 chars
    check_command(cmd, write_enabled=False, yolo_mode=False)  # should not raise


# --- read-only mode (default) ---

def test_show_command_allowed_in_read_only_mode():
    check_command("show interfaces", write_enabled=False, yolo_mode=False)


def test_show_with_args_allowed_in_read_only_mode():
    check_command("show ip route 10.0.0.0", write_enabled=False, yolo_mode=False)


def test_ping_allowed_in_read_only_mode():
    check_command("ping 192.168.1.1", write_enabled=False, yolo_mode=False)


def test_write_command_blocked_in_read_only_mode():
    with pytest.raises(CLIPolicyViolation, match="Write commands are disabled"):
        check_command("set hostname mydevice", write_enabled=False, yolo_mode=False)


def test_configure_blocked_in_read_only_mode():
    with pytest.raises(CLIPolicyViolation, match="Write commands are disabled"):
        check_command("configure terminal", write_enabled=False, yolo_mode=False)


# --- write enabled mode ---

def test_write_command_allowed_when_write_enabled():
    check_command("set hostname mydevice", write_enabled=True, yolo_mode=False)


def test_denied_command_blocked_even_when_write_enabled():
    with pytest.raises(CLIPolicyViolation, match="deny list"):
        check_command("reload", write_enabled=True, yolo_mode=False)


def test_factory_reset_blocked_when_write_enabled():
    with pytest.raises(CLIPolicyViolation, match="deny list"):
        check_command("factory-reset", write_enabled=True, yolo_mode=False)


def test_erase_config_blocked_when_write_enabled():
    with pytest.raises(CLIPolicyViolation, match="deny list"):
        check_command("write erase", write_enabled=True, yolo_mode=False)


# --- yolo mode ---

def test_yolo_bypasses_read_only_check():
    check_command("configure terminal", write_enabled=False, yolo_mode=True)


def test_yolo_bypasses_deny_list():
    check_command("reload", write_enabled=True, yolo_mode=True)


def test_yolo_still_rejects_empty_command():
    with pytest.raises(CLIPolicyViolation, match="empty"):
        check_command("", write_enabled=True, yolo_mode=True)


def test_yolo_still_enforces_max_length():
    long_cmd = "reload " + "x" * 600
    with pytest.raises(CLIPolicyViolation, match="maximum length"):
        check_command(long_cmd, write_enabled=True, yolo_mode=True, max_length=512)


# --- custom deny list ---

def test_custom_deny_list_blocks_command():
    with pytest.raises(CLIPolicyViolation, match="deny list"):
        check_command(
            "clear counters",
            write_enabled=True,
            yolo_mode=False,
            extra_deny=frozenset({"clear counters"}),
        )


def test_custom_deny_list_is_additive_to_defaults():
    custom = frozenset({"clear counters"})
    # reload is in defaults, should still be blocked
    with pytest.raises(CLIPolicyViolation, match="deny list"):
        check_command("reload", write_enabled=True, yolo_mode=False, extra_deny=custom)


# --- permit list ---

def test_permit_list_allows_listed_command():
    check_command(
        "show version",
        write_enabled=True,
        yolo_mode=False,
        permit_list=frozenset({"show version"}),
    )


def test_permit_list_blocks_unlisted_command():
    with pytest.raises(CLIPolicyViolation, match="not in the permit list"):
        check_command(
            "show interfaces",
            write_enabled=True,
            yolo_mode=False,
            permit_list=frozenset({"show version"}),
        )


def test_permit_list_allows_prefix_match():
    # "show" in permit list should allow "show interfaces"
    check_command(
        "show interfaces",
        write_enabled=True,
        yolo_mode=False,
        permit_list=frozenset({"show"}),
    )


def test_permit_list_prefix_does_not_allow_unrelated_command():
    with pytest.raises(CLIPolicyViolation, match="not in the permit list"):
        check_command(
            "set hostname mydevice",
            write_enabled=True,
            yolo_mode=False,
            permit_list=frozenset({"show"}),
        )


def test_shutdown_suffix_not_blocked_by_deny_list():
    # "no shutdown" should NOT be blocked, "shutdown" is in deny list
    # but endswith check was removed; only exact match and startswith apply
    check_command("no shutdown", write_enabled=True, yolo_mode=False)


# --- deny list constants ---

def test_default_deny_contains_expected_commands():
    assert "reload" in DEFAULT_DENY_COMMANDS
    assert "factory-reset" in DEFAULT_DENY_COMMANDS
    assert "write erase" in DEFAULT_DENY_COMMANDS
    assert "erase startup-config" in DEFAULT_DENY_COMMANDS
