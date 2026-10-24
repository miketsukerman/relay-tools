"""Parse one boot's console log into a compact boot report."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from relay_tools.board_config import ConsoleConfig

from .store import BootRecord, LogLine, utc_now

MAX_REPORTED_ISSUES = 20
ERROR_CONTEXT_LINES = 5
NO_KERNEL_WINDOW_S = 30.0
IN_PROGRESS_MAX_AGE_S = 300.0

_UBOOT_VERSION_RE = re.compile(r"^U-Boot ")
_KERNEL_VERSION_RE = re.compile(r"Linux version ")
_STARTING_KERNEL_RE = re.compile(r"Starting kernel")
_KERNEL_PANIC_RE = re.compile(r"Kernel panic")
_IPV4_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
_ADDRESS_HINT_RE = re.compile(r"inet |IP address|ipaddr=|DHCP|bound to")

DEFAULT_LOGIN_PROMPT = r"login:"
DEFAULT_UBOOT_PROMPT = r"=> $"


def _elapsed(start: datetime, moment: datetime) -> float:
    return round((moment - start).total_seconds(), 1)


def build_report(
    lines: list[LogLine],
    record: BootRecord,
    *,
    config: ConsoleConfig | None = None,
    board: str = "",
    is_current: bool = False,
) -> dict[str, Any]:
    """Build the boot report for one boot's sanitized log lines."""

    login_re = re.compile(
        (config.login_prompt if config and config.login_prompt else None)
        or DEFAULT_LOGIN_PROMPT
    )
    uboot_re = re.compile(
        (config.uboot_prompt if config and config.uboot_prompt else None)
        or DEFAULT_UBOOT_PROMPT
    )
    error_res = [
        re.compile(p)
        for p in (config.error_patterns if config else ())
    ]
    warning_res = [
        re.compile(p)
        for p in (config.warning_patterns if config else ())
    ]

    uboot_version: str | None = None
    kernel_version: str | None = None
    times: dict[str, float] = {}
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    error_count = 0
    warning_count = 0
    addresses: list[str] = []
    saw_panic = False
    saw_login = False
    saw_uboot_prompt = False
    starting_kernel_elapsed: float | None = None

    start = lines[0].timestamp if lines else None
    for index, line in enumerate(lines):
        text = line.text
        elapsed = _elapsed(start, line.timestamp) if start else 0.0
        if uboot_version is None and _UBOOT_VERSION_RE.match(text):
            uboot_version = text
            times.setdefault("uboot", elapsed)
        if kernel_version is None:
            match = _KERNEL_VERSION_RE.search(text)
            if match:
                kernel_version = text[match.start():]
        if (
            starting_kernel_elapsed is None
            and (_STARTING_KERNEL_RE.search(text) or _KERNEL_VERSION_RE.search(text))
        ):
            starting_kernel_elapsed = elapsed
            times.setdefault("kernel_start", elapsed)
        if _KERNEL_PANIC_RE.search(text):
            saw_panic = True
        if not saw_login and login_re.search(text):
            saw_login = True
            times.setdefault("login", elapsed)
        if uboot_re.search(text):
            saw_uboot_prompt = True
        if _ADDRESS_HINT_RE.search(text):
            for address in _IPV4_RE.findall(text):
                if address.startswith("127.") or address == "0.0.0.0":
                    continue
                if address not in addresses:
                    addresses.append(address)
        matched_error = any(p.search(text) for p in error_res)
        if matched_error:
            error_count += 1
            if len(errors) < MAX_REPORTED_ISSUES:
                context = [
                    entry.text
                    for entry in lines[max(0, index - ERROR_CONTEXT_LINES):index]
                ]
                errors.append(
                    {"line": line.number, "text": text, "context": context}
                )
        elif any(p.search(text) for p in warning_res):
            warning_count += 1
            if len(warnings) < MAX_REPORTED_ISSUES:
                warnings.append({"line": line.number, "text": text})

    end = utc_now() if is_current else (lines[-1].timestamp if lines else None)
    boot_age_s = (end - start).total_seconds() if start and end else 0.0

    result = classify_result(
        saw_panic=saw_panic,
        saw_login=saw_login,
        saw_uboot_prompt=saw_uboot_prompt,
        starting_kernel_elapsed=starting_kernel_elapsed,
        boot_age_s=boot_age_s,
        is_current=is_current,
    )

    trigger = record.trigger
    if record.reason:
        trigger = f"{record.trigger}: {record.reason}"
    if record.actor:
        trigger += f" by {record.actor}"

    return {
        "board": board,
        "boot": record.id,
        "trigger": trigger,
        "result": result,
        "uboot": uboot_version,
        "kernel": kernel_version,
        "times_s": times,
        "errors": errors,
        "warnings": warnings,
        "error_count": error_count,
        "warning_count": warning_count,
        "log_lines": len(lines),
        "addresses_seen": addresses,
    }


def classify_result(
    *,
    saw_panic: bool,
    saw_login: bool,
    saw_uboot_prompt: bool,
    starting_kernel_elapsed: float | None,
    boot_age_s: float,
    is_current: bool,
) -> str:
    """Classify a boot; the checks run in the documented order."""

    if saw_panic:
        return "kernel_panic"
    if saw_login:
        return "login_reached"
    if saw_uboot_prompt:
        return "uboot_prompt"
    kernel_started_in_time = (
        starting_kernel_elapsed is not None
        and starting_kernel_elapsed <= NO_KERNEL_WINDOW_S
    )
    if boot_age_s >= NO_KERNEL_WINDOW_S and not kernel_started_in_time:
        return "no_kernel"
    if is_current and boot_age_s < IN_PROGRESS_MAX_AGE_S:
        return "in_progress"
    return "incomplete"
