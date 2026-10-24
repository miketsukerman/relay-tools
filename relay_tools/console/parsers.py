"""Parsers that turn console action output into structured data."""

from __future__ import annotations

import re
from typing import Any, Callable

MAX_RAW_LINES = 200

_IPV4_ADDR_RE = re.compile(
    r"^\s*\d+:\s+(?P<interface>\S+)\s+inet\s+"
    r"(?P<address>\d{1,3}(?:\.\d{1,3}){3})/(?P<prefix>\d{1,2})"
)


def parse_ipv4(lines: list[str]) -> list[dict[str, Any]]:
    """Parse ``ip -4 -o addr show`` output, skipping loopback."""

    addresses: list[dict[str, Any]] = []
    for line in lines:
        match = _IPV4_ADDR_RE.match(line)
        if not match:
            continue
        interface = match.group("interface")
        address = match.group("address")
        if interface == "lo" or address.startswith("127."):
            continue
        addresses.append(
            {
                "interface": interface,
                "address": address,
                "prefix": int(match.group("prefix")),
            }
        )
    return addresses


def parse_systemd(lines: list[str]) -> dict[str, Any]:
    """Parse ``systemctl is-system-running`` plus ``systemctl --failed``."""

    state = None
    failed_units: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if state is None:
            state = stripped
            continue
        unit = stripped.split()[0]
        if "." in unit:
            failed_units.append(unit)
    return {"state": state or "unknown", "failed_units": failed_units}


def parse_kv(lines: list[str]) -> dict[str, str]:
    """Parse ``KEY=value`` lines into a mapping, stripping quotes."""

    mapping: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if not key or " " in key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        mapping[key] = value
    return mapping


def parse_lines(lines: list[str]) -> list[str]:
    """Return the raw lines, capped at 200."""

    return lines[:MAX_RAW_LINES]


PARSERS: dict[str, Callable[[list[str]], Any]] = {
    "ipv4": parse_ipv4,
    "systemd": parse_systemd,
    "kv": parse_kv,
    "lines": parse_lines,
}


def parse_output(parse: str, lines: list[str]) -> Any:
    parser = PARSERS.get(parse)
    if parser is None:
        raise ValueError(f"unknown parser {parse!r}")
    return parser(lines)
