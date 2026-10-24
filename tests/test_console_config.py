"""Tests for the ``console:`` block of board profiles."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from relay_tools.board_config import (
    BUILTIN_CONSOLE_ACTIONS,
    BoardConfigError,
    load_board_profile,
)

BASE_YAML = """
name: lab
switches:
  power:
    channel: 1
defaults:
  power_switch: power
"""


def _write(tmp_path: Path, console_yaml: str) -> Path:
    path = tmp_path / "lab.yaml"
    body = BASE_YAML
    if console_yaml:
        body += "console:\n" + textwrap.indent(
            textwrap.dedent(console_yaml), "  "
        )
    path.write_text(body)
    return path


def test_profile_without_console_block(tmp_path):
    profile = load_board_profile(_write(tmp_path, ""))
    assert profile.console is None


def test_minimal_console_block_defaults(tmp_path):
    profile = load_board_profile(_write(tmp_path, "device: /dev/pts/5\n"))
    console = profile.console
    assert console is not None
    assert console.device == "/dev/pts/5"
    assert console.baud == 115200
    assert console.data_bits == 8
    assert console.parity == "none"
    assert console.stop_bits == 1
    assert console.retention.max_boots == 200
    assert console.retention.max_megabytes == 200
    assert console.mirror_port is None
    assert console.login is None
    # Built-in actions are merged in.
    assert set(BUILTIN_CONSOLE_ACTIONS) <= set(console.actions)
    assert console.actions["ip_address"].parse == "ipv4"
    assert console.actions["uboot_net"].context == "uboot"


def test_full_console_block(tmp_path):
    profile = load_board_profile(
        _write(
            tmp_path,
            """
            device: /dev/serial/by-id/usb-FTDI-if00-port0
            baud: 57600
            data_bits: 7
            parity: even
            stop_bits: 2
            boot_banner: "U-Boot SPL"
            prompts:
              uboot: "=> "
              login: "login:"
            patterns:
              errors: ["my-error"]
              warnings: ["my-warning"]
            redact: ["(?i)secret\\\\S+"]
            retention:
              max_boots: 10
              max_megabytes: 5
            mirror_port: 7001
            login:
              user: root
              password_file: /etc/relay/secrets/lab.console
              shell_prompt: "[#$] $"
              timeout_s: 7
              logout_after: true
            actions:
              custom:
                context: linux
                command: "cat /proc/loadavg"
                parse: lines
                timeout_s: 3
                description: Load average
              dmesg_tail: null
            """,
        )
    )
    console = profile.console
    assert console is not None
    assert console.baud == 57600
    assert console.boot_banner == "U-Boot SPL"
    assert console.uboot_prompt == "=> "
    assert console.login_prompt == "login:"
    assert "my-error" in console.error_patterns
    # Built-in patterns stay merged.
    assert any("Kernel panic" in pat for pat in console.error_patterns)
    assert "my-warning" in console.warning_patterns
    assert console.redact == ("(?i)secret\\S+",)
    assert console.retention.max_boots == 10
    assert console.mirror_port == 7001
    assert console.login is not None
    assert console.login.user == "root"
    assert console.login.password_file == "/etc/relay/secrets/lab.console"
    assert console.login.logout_after is True
    assert console.actions["custom"].command == "cat /proc/loadavg"
    assert "dmesg_tail" not in console.actions  # disabled with null
    assert "ip_address" in console.actions


def test_device_urls_accepted(tmp_path):
    for device in ("rfc2217://127.0.0.1:2001", "socket://127.0.0.1:2001"):
        profile = load_board_profile(_write(tmp_path, f"device: {device}\n"))
        assert profile.console is not None
        assert profile.console.device == device


def test_ttyusb_device_warns_but_loads(tmp_path, caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        profile = load_board_profile(
            _write(tmp_path, "device: /dev/ttyUSB0\n")
        )
    assert profile.console is not None
    assert any("by-id" in rec.message for rec in caplog.records)


@pytest.mark.parametrize(
    ("console_yaml", "path_fragment"),
    [
        ("baud: 115200\n", "console.device"),
        ("device: relative/path\n", "console.device"),
        ("device: /dev/pts/5\nbaud: 12345\n", "console.baud"),
        ("device: /dev/pts/5\ndata_bits: 9\n", "console.data_bits"),
        ("device: /dev/pts/5\nparity: strange\n", "console.parity"),
        ("device: /dev/pts/5\nstop_bits: 3\n", "console.stop_bits"),
        ("device: /dev/pts/5\nboot_banner: '('\n", "console.boot_banner"),
        (
            "device: /dev/pts/5\nprompts:\n  uboot: '('\n",
            "console.prompts.uboot",
        ),
        ("device: /dev/pts/5\nredact: ['(']\n", "console.redact"),
        ("device: /dev/pts/5\nmirror_port: 80\n", "console.mirror_port"),
        (
            "device: /dev/pts/5\nretention:\n  max_boots: 0\n",
            "console.retention.max_boots",
        ),
        (
            "device: /dev/pts/5\nlogin:\n  user: root\n"
            "  password_file: relative\n",
            "console.login.password_file",
        ),
        (
            "device: /dev/pts/5\nlogin:\n  user: root\n"
            "  password_file: /a\n  password_env: B\n",
            "console.login",
        ),
        (
            "device: /dev/pts/5\nactions:\n  UPPER:\n    command: ls\n",
            "console.actions",
        ),
        (
            "device: /dev/pts/5\nactions:\n  a:\n    command: \"x\\ny\"\n",
            "console.actions.a.command",
        ),
        (
            "device: /dev/pts/5\nactions:\n  a:\n    command: ls\n"
            "    context: windows\n",
            "console.actions.a.context",
        ),
        (
            "device: /dev/pts/5\nactions:\n  a:\n    command: ls\n"
            "    parse: xml\n",
            "console.actions.a.parse",
        ),
        (
            "device: /dev/pts/5\nactions:\n  a:\n    command: ls\n"
            "    timeout_s: 0\n",
            "console.actions.a.timeout_s",
        ),
    ],
)
def test_console_validation_errors(tmp_path, console_yaml, path_fragment):
    with pytest.raises(BoardConfigError) as excinfo:
        load_board_profile(_write(tmp_path, console_yaml))
    assert path_fragment in str(excinfo.value)


def test_command_length_limit(tmp_path):
    long_command = "echo " + "x" * 300
    with pytest.raises(BoardConfigError) as excinfo:
        load_board_profile(
            _write(
                tmp_path,
                "device: /dev/pts/5\nactions:\n  a:\n"
                f"    command: \"{long_command}\"\n",
            )
        )
    assert "console.actions.a.command" in str(excinfo.value)


@pytest.mark.parametrize(
    "command",
    [
        "echo hi > /tmp/x",
        "rm -rf /",
        "dd if=/dev/zero of=/dev/mmcblk0",
        "reboot",
        "setenv bootargs quiet",
        "mmc write 0 0 0",
        "cat script | sh",
    ],
)
def test_mutation_lint_refuses(tmp_path, command):
    with pytest.raises(BoardConfigError) as excinfo:
        load_board_profile(
            _write(
                tmp_path,
                "device: /dev/pts/5\nactions:\n  a:\n"
                f"    command: \"{command}\"\n",
            )
        )
    assert "mutating" in str(excinfo.value)


def test_mutation_lint_allows_with_flag(tmp_path):
    profile = load_board_profile(
        _write(
            tmp_path,
            "device: /dev/pts/5\nactions:\n  wipe:\n"
            "    command: \"reboot\"\n    mutating: true\n",
        )
    )
    assert profile.console is not None
    assert profile.console.actions["wipe"].mutating is True


def test_password_file_not_read_at_parse_time(tmp_path):
    # The file does not exist; parsing must still succeed.
    profile = load_board_profile(
        _write(
            tmp_path,
            "device: /dev/pts/5\nlogin:\n  user: root\n"
            "  password_file: /nonexistent/secret\n",
        )
    )
    assert profile.console is not None
    assert profile.console.login is not None
    assert profile.console.login.password_file == "/nonexistent/secret"
