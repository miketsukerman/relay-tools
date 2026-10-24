"""Tests for console line sanitizing and action output parsers."""

from __future__ import annotations

from relay_tools.console.parsers import (
    parse_ipv4,
    parse_kv,
    parse_lines,
    parse_output,
    parse_systemd,
)
from relay_tools.console.sanitize import Redactor, sanitize_line


def test_ansi_escapes_stripped():
    assert sanitize_line(b"\x1b[31mred\x1b[0m text") == "red text"


def test_control_characters_dropped_except_tab():
    assert sanitize_line(b"a\x00b\x07c\td\r") == "abc\td"


def test_invalid_utf8_replaced():
    assert "\ufffd" in sanitize_line(b"bad \xff\xfe bytes")


def test_long_lines_truncated():
    out = sanitize_line(b"x" * 3000)
    assert out.endswith("\u2026[truncated]")
    assert len(out) <= 2000 + len("\u2026[truncated]")


def test_redaction():
    redactor = Redactor([r"(?i)password[:=]\s*\S+"])
    assert (
        sanitize_line(b"Password: hunter2", redactor) == "[REDACTED]"
    )


def test_redactor_add_literal():
    redactor = Redactor()
    redactor.add_literal("s3cr(t")
    assert redactor.apply("the s3cr(t leaked") == "the [REDACTED] leaked"


def test_parse_ipv4():
    lines = [
        "1: lo    inet 127.0.0.1/8 scope host lo",
        "2: eth0    inet 192.168.3.141/24 brd 192.168.3.255 scope global",
        "3: wlan0    inet 10.0.0.7/16 scope global wlan0",
    ]
    assert parse_ipv4(lines) == [
        {"interface": "eth0", "address": "192.168.3.141", "prefix": 24},
        {"interface": "wlan0", "address": "10.0.0.7", "prefix": 16},
    ]


def test_parse_systemd():
    lines = [
        "degraded",
        "weston.service loaded failed failed Weston compositor",
    ]
    result = parse_systemd(lines)
    assert result["state"] == "degraded"
    assert result["failed_units"] == ["weston.service"]


def test_parse_kv():
    lines = ['NAME="Poky"', "ID=poky", "ipaddr=192.168.3.141", "garbage"]
    assert parse_kv(lines) == {
        "NAME": "Poky",
        "ID": "poky",
        "ipaddr": "192.168.3.141",
    }


def test_parse_lines_capped():
    assert len(parse_lines([f"l{i}" for i in range(500)])) == 200


def test_parse_output_dispatch():
    assert parse_output("kv", ["a=b"]) == {"a": "b"}
    assert parse_output("lines", ["x"]) == ["x"]
