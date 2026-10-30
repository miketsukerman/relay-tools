"""Tests for the relay-mcp console tools."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

pytest.importorskip("mcp")

from relay_tools.mcp.server import (  # noqa: E402
    RelayMCPService,
    Settings,
    ToolError,
)

PROFILE_YAML = """
name: lab
switches:
  power:
    channel: 1
defaults:
  power_switch: power
console:
  device: /dev/pts/5
  actions:
    wipe_env:
      command: "reboot"
      mutating: true
      description: Dangerous
"""

PLAIN_YAML = """
name: plain
switches:
  power:
    channel: 1
"""


class FakeConsoleService:
    """In-memory relay-console implementing the endpoints over httpx."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.report_addresses: list[str] = ["192.168.3.141"]
        self.wait_matched = True
        self.on_wait = None
        self.action_result: dict = {
            "board": "lab",
            "action": "ip_address",
            "state_before": "linux_shell",
            "logged_in": False,
            "exit_code": 0,
            "parsed": [
                {"interface": "eth0", "address": "192.168.3.141",
                 "prefix": 24}
            ],
            "untrusted_output": ["inet 192.168.3.141/24"],
            "duration_s": 1.2,
        }

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/consoles/lab/boots":
            return httpx.Response(
                200,
                json={
                    "board": "lab",
                    "boots": [{"id": 2, "trigger": "marker",
                               "result": "login_reached"}],
                },
            )
        if path.endswith("/log"):
            return httpx.Response(
                200,
                json={
                    "board": "lab",
                    "boot": 2,
                    "total_lines": 5,
                    "untrusted_lines": [
                        {"line": 1, "timestamp": "t", "text": "hello"}
                    ],
                },
            )
        if path.endswith("/report"):
            return httpx.Response(
                200,
                json={
                    "board": "lab",
                    "boot": 2,
                    "result": "login_reached",
                    "addresses_seen": list(self.report_addresses),
                },
            )
        if path == "/consoles/lab/wait":
            if self.on_wait is not None:
                self.on_wait()
            return httpx.Response(
                200,
                json={
                    "board": "lab",
                    "matched": self.wait_matched,
                    "match": "login:" if self.wait_matched else None,
                    "elapsed_s": 3.0,
                    "untrusted_lines": ["before"],
                },
            )
        if path == "/consoles/lab/state":
            return httpx.Response(
                200,
                json={
                    "board": "lab",
                    "state": "linux_shell",
                    "untrusted_lines": ["# "],
                },
            )
        if path.startswith("/consoles/lab/actions/"):
            return httpx.Response(200, json=self.action_result)
        if path == "/consoles/lab/markers":
            return httpx.Response(200, json={"board": "lab", "boot": 3})
        return httpx.Response(404, json={"detail": "not found"})


@pytest.fixture()
def board_dir(tmp_path: Path) -> Path:
    config_dir = tmp_path / "boards.d"
    config_dir.mkdir()
    (config_dir / "lab.yaml").write_text(PROFILE_YAML)
    (config_dir / "plain.yaml").write_text(PLAIN_YAML)
    return config_dir


@pytest.fixture()
def console_service() -> FakeConsoleService:
    return FakeConsoleService()


def _service(board_dir, console_service, tmp_path, **overrides):
    transport = console_service.transport()
    settings = Settings(
        config_dir=board_dir,
        audit_log=tmp_path / "audit.jsonl",
        console_client_factory=lambda **kwargs: httpx.Client(
            transport=transport, **kwargs
        ),
        **overrides,
    )
    return RelayMCPService(settings)


@pytest.fixture()
def service(board_dir, console_service, tmp_path) -> RelayMCPService:
    return _service(board_dir, console_service, tmp_path)


def _audit_entries(service: RelayMCPService) -> list[dict]:
    path = service.audit.path
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def test_list_boots(service):
    result = service.list_boots("lab")
    assert result["boots"][0]["result"] == "login_reached"
    entry = _audit_entries(service)[-1]
    assert entry["tool"] == "list_boots"
    assert entry["kind"] == "read"
    assert entry["audit_id"]


def test_board_without_console_block(service):
    with pytest.raises(ToolError, match="no console block"):
        service.list_boots("plain")


def test_console_log_untrusted_field_and_validation(service):
    result = service.console_log("lab")
    assert result["untrusted_log"][0]["text"] == "hello"
    assert "untrusted_lines" not in result

    with pytest.raises(ToolError, match="boot selector"):
        service.console_log("lab", boot="1; rm")
    with pytest.raises(ToolError, match="grep"):
        service.console_log("lab", grep="(")
    with pytest.raises(ToolError, match="grep"):
        service.console_log("lab", grep="x" * 300)


def test_boot_report(service):
    result = service.boot_report("lab", "2")
    assert result["result"] == "login_reached"
    with pytest.raises(ToolError):
        service.boot_report("lab", "not-a-boot")


def test_wait_for_console_caps_timeout(service, console_service):
    result = service.wait_for_console("lab", timeout_s=9999)
    assert result["matched"] is True
    assert result["untrusted_log"] == ["before"]
    request = console_service.requests[-1]
    assert request.url.params["timeout_s"] == "600"
    with pytest.raises(ToolError, match="pattern"):
        service.wait_for_console("lab", pattern="(")


def test_console_state(service):
    result = service.console_state("lab")
    assert result["state"] == "linux_shell"
    assert result["untrusted_log"] == ["# "]


def test_console_cooldown(service):
    service.console_state("lab")
    with pytest.raises(ToolError, match="cooldown"):
        service.run_console_action("lab", "system_status")


def test_run_console_action(service, console_service):
    result = service.run_console_action("lab", "ip_address")
    assert result["parsed"][0]["address"] == "192.168.3.141"
    request = console_service.requests[-1]
    assert request.url.path == "/consoles/lab/actions/ip_address"
    assert request.headers["x-relay-actor"] == "relay-mcp"
    assert request.headers["x-relay-audit-id"]
    entry = _audit_entries(service)[-1]
    assert entry["kind"] == "console"


def test_unknown_action_lists_valid_ones(service):
    with pytest.raises(ToolError, match="ip_address"):
        service.run_console_action("lab", "nope")


def test_mutating_actions_hidden_and_refused(
    board_dir, console_service, tmp_path
):
    service = _service(board_dir, console_service, tmp_path)
    names = {
        action["name"]
        for action in service.list_console_actions("lab")["actions"]
    }
    assert "wipe_env" not in names
    with pytest.raises(ToolError, match="mutating"):
        service.run_console_action("lab", "wipe_env")

    allowed = _service(
        board_dir,
        console_service,
        tmp_path,
        allow_mutating_console_actions=True,
    )
    names = {
        action["name"]
        for action in allowed.list_console_actions("lab")["actions"]
    }
    assert "wipe_env" in names


def test_read_only_mode_refuses_console_actions(
    board_dir, console_service, tmp_path
):
    service = _service(
        board_dir, console_service, tmp_path, read_only=True
    )
    with pytest.raises(ToolError, match="read-only"):
        service.run_console_action("lab", "ip_address")
    # Log reads still work in read-only mode.
    assert service.list_boots("lab")["boots"]


def test_dut_ip_address_happy_path(service):
    result = service.dut_ip_address("lab")
    assert result["addresses"][0]["address"] == "192.168.3.141"
    assert result["state"] == "linux_shell"


def test_dut_ip_address_fallback_when_not_in_linux(
    board_dir, console_service, tmp_path
):
    console_service.action_result = {
        "board": "lab",
        "action": "ip_address",
        "error": "wrong_context",
        "message": "board is at the U-Boot prompt",
        "state": "uboot",
    }
    service = _service(board_dir, console_service, tmp_path)
    result = service.dut_ip_address("lab")
    assert result["addresses"] == []
    assert result["state"] == "uboot"
    assert result["addresses_seen"] == ["192.168.3.141"]
    assert "hint" in result


def _not_in_linux(console_service):
    console_service.action_result = {
        "board": "lab",
        "action": "ip_address",
        "error": "wrong_context",
        "message": "board is still booting",
        "state": "booting",
    }
    console_service.report_addresses = []


def test_dut_ip_address_wait_s_zero_keeps_old_payload(service):
    result = service.dut_ip_address("lab", wait_s=0)
    assert result["addresses"][0]["address"] == "192.168.3.141"
    assert "found" not in result
    assert "waited_s" not in result


def test_dut_ip_address_waits_until_address_appears(
    board_dir, console_service, tmp_path
):
    _not_in_linux(console_service)

    def _address_appears():
        console_service.report_addresses = ["192.168.3.141"]

    console_service.on_wait = _address_appears
    service = _service(board_dir, console_service, tmp_path)
    result = service.dut_ip_address("lab", wait_s=120)
    assert result["found"] is True
    assert result["addresses_seen"] == ["192.168.3.141"]
    assert result["waited_s"] < 120
    wait_requests = [
        request
        for request in console_service.requests
        if request.url.path == "/consoles/lab/wait"
    ]
    assert len(wait_requests) == 1


def test_dut_ip_address_wait_no_address_reports_hint(
    board_dir, console_service, tmp_path
):
    _not_in_linux(console_service)
    console_service.wait_matched = False
    service = _service(board_dir, console_service, tmp_path)
    result = service.dut_ip_address("lab", wait_s=5)
    assert result["found"] is False
    assert result["addresses"] == []
    assert result["hint"] == (
        "no address yet; check boot_report or wait_for_console"
    )
    assert "waited_s" in result


def test_dut_ip_address_wait_s_is_clamped(
    board_dir, console_service, tmp_path
):
    _not_in_linux(console_service)
    console_service.wait_matched = False
    service = _service(board_dir, console_service, tmp_path)
    service.dut_ip_address("lab", wait_s=9999)
    wait_request = next(
        request
        for request in console_service.requests
        if request.url.path == "/consoles/lab/wait"
    )
    assert float(wait_request.url.params["timeout_s"]) <= 300


def test_dut_ip_address_wait_skips_wait_when_address_known(
    service, console_service
):
    result = service.dut_ip_address("lab", wait_s=120)
    assert result["found"] is True
    assert result["addresses"][0]["address"] == "192.168.3.141"
    assert all(
        request.url.path != "/consoles/lab/wait"
        for request in console_service.requests
    )


def test_dut_ip_address_wait_respects_console_cooldown(service):
    service.console_state("lab")
    with pytest.raises(ToolError, match="cooldown"):
        service.dut_ip_address("lab", wait_s=5)


def test_dut_ip_address_wait_respects_board_lock(service):
    lock = service._board_lock("lab")
    assert lock.acquire(blocking=False)
    try:
        with pytest.raises(ToolError, match="busy"):
            service.dut_ip_address("lab", wait_s=5)
        # wait_s=0 keeps today's behaviour and does not take the lock.
        assert service.dut_ip_address("lab")["addresses"]
    finally:
        lock.release()


def test_service_unreachable_maps_to_tool_error(board_dir, tmp_path):
    def _refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    settings = Settings(
        config_dir=board_dir,
        audit_log=tmp_path / "audit.jsonl",
        console_client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(_refuse), **kwargs
        ),
    )
    service = RelayMCPService(settings)
    with pytest.raises(ToolError, match="relay-console unreachable"):
        service.list_boots("lab")
    entry = _audit_entries(service)[-1]
    assert entry["ok"] is False
