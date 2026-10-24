"""Tests for the console boot store and the boot report parser."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from relay_tools.board_config import (
    DEFAULT_CONSOLE_ERROR_PATTERNS,
    DEFAULT_CONSOLE_WARNING_PATTERNS,
    ConsoleConfig,
    ConsoleRetentionConfig,
)
from relay_tools.console.report import build_report
from relay_tools.console.store import BootRecord, BootStore, LogLine

CONFIG = ConsoleConfig(device="/dev/pts/9")

START = datetime(2026, 10, 23, 20, 51, 7, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# BootStore
# ---------------------------------------------------------------------------


def test_lines_written_with_timestamps(tmp_path):
    store = BootStore(tmp_path / "lab")
    store.new_boot(trigger="service_start")
    store.append_line("hello world", moment=START)
    store.close()
    log = (tmp_path / "lab" / "boots" / "000001.log").read_text()
    assert log == "2026-10-23T20:51:07.000Z\thello world\n"


def test_boot_ids_continue_after_restart(tmp_path):
    store = BootStore(tmp_path / "lab")
    store.new_boot(trigger="service_start")
    store.new_boot(trigger="marker", reason="power-cycle", actor="x")
    store.close()

    store2 = BootStore(tmp_path / "lab")
    record = store2.new_boot(trigger="service_start")
    assert record.id == 3
    # The previous current boot was finalized, not left in_progress.
    older = {rec.id: rec for rec in store2.records()}
    assert older[2].result != "in_progress"
    store2.close()


def test_record_fields_and_find(tmp_path):
    store = BootStore(tmp_path / "lab")
    record = store.new_boot(
        trigger="marker", reason="boot-emmc", actor="relay-mcp",
        audit_id="1234",
    )
    store.append_line("one")
    assert record.trigger == "marker"
    assert record.reason == "boot-emmc"
    assert record.actor == "relay-mcp"
    assert record.audit_id == "1234"
    assert store.find("last").id == record.id
    assert store.find(str(record.id)).id == record.id
    assert store.find("999") is None
    assert store.current.result == "in_progress"
    store.close()


def test_retention_by_count(tmp_path):
    store = BootStore(
        tmp_path / "lab",
        retention=ConsoleRetentionConfig(max_boots=3, max_megabytes=200),
    )
    for _ in range(6):
        store.new_boot(trigger="detected")
        store.append_line("x")
    records = store.records()
    assert len(records) == 3
    # The newest (current) boot is always kept.
    assert records[0].id == 6
    assert not (tmp_path / "lab" / "boots" / "000001.log").exists()
    store.close()


def test_retention_by_size_never_deletes_current(tmp_path):
    store = BootStore(
        tmp_path / "lab",
        retention=ConsoleRetentionConfig(max_boots=200, max_megabytes=1),
    )
    chunk = "y" * 1000
    for _ in range(3):
        store.new_boot(trigger="detected")
        for _ in range(400):
            store.append_line(chunk)
    # Retention runs when the next boot starts; by then boots 1-3 hold
    # ~1.2 MB, so the oldest is deleted while the new current one stays.
    store.new_boot(trigger="detected")
    records = store.records()
    ids = [rec.id for rec in records]
    assert 4 in ids  # current boot survives
    assert 1 not in ids
    assert not (tmp_path / "lab" / "boots" / "000001.log").exists()
    store.close()


def test_records_newest_first_with_limit(tmp_path):
    store = BootStore(tmp_path / "lab")
    for _ in range(4):
        store.new_boot(trigger="detected")
    records = store.records(2)
    assert [rec.id for rec in records] == [4, 3]
    store.close()


# ---------------------------------------------------------------------------
# Boot report
# ---------------------------------------------------------------------------


def _lines(entries: list[tuple[float, str]]) -> list[LogLine]:
    return [
        LogLine(
            timestamp=START + timedelta(seconds=offset),
            text=text,
            number=number,
        )
        for number, (offset, text) in enumerate(entries, start=1)
    ]


def _record(**kwargs) -> BootRecord:
    defaults = dict(
        id=42,
        started_at=START,
        trigger="marker",
        reason="boot-emmc",
        actor="relay-mcp",
    )
    defaults.update(kwargs)
    return BootRecord(**defaults)


GOOD_BOOT = [
    (0.0, "U-Boot SPL 2025.04 (Oct 01 2025)"),
    (1.9, "U-Boot 2025.04 for imx93 board"),
    (3.0, "Starting kernel ..."),
    (3.4, "[    0.000000] Linux version 6.18.20 (gcc 13)"),
    (8.0, "[    4.2] imx93 eth0: inet 192.168.3.141/24 via DHCP"),
    (14.8, "imx93 login:"),
]


def test_report_good_boot():
    report = build_report(
        _lines(GOOD_BOOT), _record(), config=CONFIG, board="rom2820"
    )
    assert report["board"] == "rom2820"
    assert report["boot"] == 42
    assert report["trigger"] == "marker: boot-emmc by relay-mcp"
    assert report["result"] == "login_reached"
    # Per spec the uboot field is the first line matching ^U-Boot ,
    # which here is the SPL banner.
    assert report["uboot"].startswith("U-Boot SPL 2025.04")
    assert "Linux version 6.18.20" in report["kernel"]
    assert report["times_s"]["uboot"] == 0.0
    assert report["times_s"]["kernel_start"] == 3.0
    assert report["times_s"]["login"] == 14.8
    assert report["error_count"] == 0
    assert report["addresses_seen"] == ["192.168.3.141"]
    assert report["log_lines"] == len(GOOD_BOOT)


def test_report_kernel_panic():
    lines = _lines(
        GOOD_BOOT[:4]
        + [(6.0, "Kernel panic - not syncing: VFS: Unable to mount root")]
    )
    report = build_report(lines, _record(), config=CONFIG)
    assert report["result"] == "kernel_panic"
    assert report["error_count"] >= 1


def test_report_failed_unit_with_context():
    lines = _lines(
        GOOD_BOOT[:4]
        + [
            (9.0, "ctx1"),
            (9.1, "ctx2"),
            (10.0, "[FAILED] Failed to start weston.service"),
            (14.8, "imx93 login:"),
        ]
    )
    report = build_report(lines, _record(), config=CONFIG)
    assert report["result"] == "login_reached"
    assert report["error_count"] == 1
    error = report["errors"][0]
    assert "weston.service" in error["text"]
    assert "ctx2" in error["context"]


def test_report_stuck_at_uboot():
    lines = _lines(
        [
            (0.0, "U-Boot 2025.04 for imx93 board"),
            (2.0, "Hit any key to stop autoboot:  0"),
            (2.5, "=> "),
        ]
    )
    report = build_report(lines, _record(), config=CONFIG)
    assert report["result"] == "uboot_prompt"


def test_report_no_kernel():
    lines = _lines(
        [
            (0.0, "U-Boot 2025.04 for imx93 board"),
            (40.0, "still spinning"),
        ]
    )
    report = build_report(lines, _record(), config=CONFIG)
    assert report["result"] == "no_kernel"


def test_report_in_progress():
    lines = _lines(GOOD_BOOT[:3])
    record = _record(started_at=datetime.now(timezone.utc))
    # Make the lines recent so boot age stays under the window.
    now = datetime.now(timezone.utc)
    recent = [
        LogLine(timestamp=now, text=line.text, number=line.number)
        for line in lines
    ]
    report = build_report(recent, record, config=CONFIG, is_current=True)
    assert report["result"] == "in_progress"


def test_report_caps_errors_but_counts_exactly():
    noise = [(float(i), f"[FAILED] Failed to start u{i}.service")
             for i in range(30)]
    report = build_report(_lines(noise + [(31.0, "login:")]),
                          _record(), config=CONFIG)
    assert report["error_count"] == 30
    assert len(report["errors"]) == 20


def test_report_addresses_seen_from_issue_and_dhcp():
    lines = _lines(
        [
            (0.0, "DHCPACK: bound to 192.168.3.141 -- renewal in 100s"),
            (1.0, "My IP address is 10.0.0.9"),
            (2.0, "random 1.2.3.4 with no hint"),
            (3.0, "inet 127.0.0.1/8 scope host lo"),
            (31.0, "board login:"),
        ]
    )
    report = build_report(lines, _record(), config=CONFIG)
    assert report["addresses_seen"] == ["192.168.3.141", "10.0.0.9"]


def test_default_patterns_present():
    assert any("Kernel panic" in p for p in DEFAULT_CONSOLE_ERROR_PATTERNS)
    assert any("watchdog" in p for p in DEFAULT_CONSOLE_WARNING_PATTERNS)
