"""Tests for the relay-mcp MCP server."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

pytest.importorskip("mcp")

from relay_tools.mcp.server import (  # noqa: E402
    RelayMCPService,
    Settings,
    ToolError,
    create_server,
)

PROFILE_YAML = """
name: lab-board
switches:
  power:
    channel: 5
    description: Main power rail
  boot0:
    channel: 1
  boot1:
    channel: 2
signals:
  reset_key:
    channel: 6
    description: Reset button
timings:
  reset_pulse: 0.01
  settle_delay: 0.01
  boot_wait: 0.01
defaults:
  power_switch: power
  reset_signal: reset_key
boot_modes:
  emmc:
    description: Boot from eMMC
    switches:
      boot0: off
      boot1: off
  sd:
    switches:
      boot0: on
      boot1: off
  usb-recovery:
    description: USB recovery, shorts the strap pins
    risky: true
    switches:
      boot0: on
      boot1: on
workflows:
  power-cycle:
    - name: cut-power
      action: set
      switch: power
      state: off
    - name: settle
      action: delay
      timing: settle_delay
    - name: restore-power
      action: set
      switch: power
      state: on
  enter-recovery:
    - name: strap-recovery
      action: set-boot-mode
      boot_mode: usb-recovery
"""


class FakeDaemon:
    """In-memory relay daemon implementing the /relays endpoints."""

    def __init__(self, channels: int = 8) -> None:
        self.state = {channel: False for channel in range(1, channels + 1)}
        self.calls: list[tuple[str, int]] = []
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _channel_json(self, channel: int) -> dict:
        return {"channel": channel, "on": self.state[channel]}

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/relays":
            return httpx.Response(
                200,
                json={
                    "channels": [
                        self._channel_json(channel)
                        for channel in sorted(self.state)
                    ]
                },
            )
        parts = path.strip("/").split("/")
        if parts[0] != "relays":
            return httpx.Response(404, json={"detail": "not found"})
        if parts[1] in {"on", "off"}:
            value = parts[1] == "on"
            for channel in self.state:
                self.state[channel] = value
            return httpx.Response(
                200,
                json={
                    "channels": [
                        self._channel_json(channel)
                        for channel in sorted(self.state)
                    ]
                },
            )
        channel = int(parts[1])
        if channel not in self.state:
            return httpx.Response(404, json={"detail": "unknown channel"})
        if len(parts) == 2:
            return httpx.Response(200, json=self._channel_json(channel))
        action = parts[2]
        if action == "on":
            self.state[channel] = True
        elif action == "off":
            self.state[channel] = False
        elif action == "toggle":
            self.state[channel] = not self.state[channel]
        elif action == "press":
            self.state[channel] = False
        else:
            return httpx.Response(404, json={"detail": "unknown action"})
        self.calls.append((action, channel))
        return httpx.Response(200, json=self._channel_json(channel))


@pytest.fixture()
def board_dir(tmp_path: Path) -> Path:
    config_dir = tmp_path / "boards.d"
    config_dir.mkdir()
    (config_dir / "lab.yaml").write_text(PROFILE_YAML)
    return config_dir


@pytest.fixture()
def daemon() -> FakeDaemon:
    return FakeDaemon()


def _service(
    board_dir: Path,
    daemon: FakeDaemon,
    tmp_path: Path,
    **overrides,
) -> RelayMCPService:
    transport = daemon.transport()
    settings = Settings(
        config_dir=board_dir,
        audit_log=tmp_path / "audit.jsonl",
        cooldown=overrides.pop("cooldown", 5.0),
        client_factory=lambda **kwargs: httpx.Client(
            transport=transport, **kwargs
        ),
        **overrides,
    )
    return RelayMCPService(settings)


@pytest.fixture()
def service(board_dir, daemon, tmp_path) -> RelayMCPService:
    return _service(board_dir, daemon, tmp_path)


def _audit_entries(service: RelayMCPService) -> list[dict]:
    path = service.audit.path
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# Read-only tools
# ---------------------------------------------------------------------------


def test_list_boards_reports_valid_and_invalid_profiles(
    service, board_dir
) -> None:
    (board_dir / "broken.yaml").write_text("signals: [not, a, mapping]\n")
    result = service.list_boards()
    assert [board["name"] for board in result["boards"]] == ["lab"]
    assert result["boards"][0]["boot_modes"] == ["emmc", "sd", "usb-recovery"]
    assert result["errors"][0]["name"] == "broken"
    assert "mapping" in result["errors"][0]["error"]


def test_describe_board_lists_profile_details(service) -> None:
    result = service.describe_board("lab")
    assert result["board"] == "lab"
    assert {signal["name"] for signal in result["signals"]} == {"reset_key"}
    modes = {mode["name"]: mode for mode in result["boot_modes"]}
    assert modes["usb-recovery"]["risky"] is True
    assert modes["emmc"]["risky"] is False
    assert "power-cycle" in result["workflows"]
    assert result["defaults"]["power_switch"] == "power"


def test_board_status_reports_channels_and_modes(service, daemon) -> None:
    result = service.board_status("lab")
    assert result["board"] == "lab"
    assert result["switches"]["power"] is False
    assert "emmc" in result["matching_boot_modes"]
    assert result["channels"]["5"] is False


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def test_power_cycle_switches_power_off_then_on(service, daemon) -> None:
    result = service.power_cycle("lab")
    power_calls = [call for call in daemon.calls if call[1] == 5]
    assert power_calls[0] == ("off", 5)
    assert ("on", 5) in power_calls
    assert power_calls.index(("off", 5)) < power_calls.index(("on", 5))
    assert result["ok"] is True
    assert result["final_status"]["switches"]["power"] is True
    assert [step["name"] for step in result["steps"]] == [
        "cut-power",
        "settle",
        "restore-power",
    ]


def test_set_boot_mode_emmc_sets_channels(service, daemon) -> None:
    daemon.state[1] = True
    daemon.state[2] = True
    result = service.set_boot_mode("emmc", "lab")
    assert daemon.state[1] is False
    assert daemon.state[2] is False
    assert "emmc" in result["final_status"]["matching_boot_modes"]
    status = service.board_status("lab")
    assert "emmc" in status["matching_boot_modes"]


def test_risky_boot_mode_is_refused(service, daemon) -> None:
    before = dict(daemon.state)
    with pytest.raises(ToolError, match="risky.*--force"):
        service.set_boot_mode("usb-recovery", "lab")
    with pytest.raises(ToolError, match="risky.*--force"):
        service.boot_and_wait("usb-recovery", "lab")
    assert daemon.state == before
    assert daemon.calls == []


def test_workflow_reaching_risky_mode_is_refused(service, daemon) -> None:
    before = dict(daemon.state)
    with pytest.raises(ToolError, match="risky boot mode 'usb-recovery'"):
        service.run_workflow("enter-recovery", "lab")
    assert daemon.state == before
    assert daemon.calls == []


def test_unknown_board_mode_and_workflow_are_refused(service) -> None:
    with pytest.raises(ToolError, match="unknown board"):
        service.board_status("nope")
    with pytest.raises(ToolError, match="available modes: emmc, sd"):
        service.set_boot_mode("nvme", "lab")
    with pytest.raises(ToolError, match="available workflows"):
        service.run_workflow("does-not-exist", "lab")


@pytest.mark.parametrize(
    "board_name",
    ["../etc/passwd", "a/b", "a\\b", "..", ".hidden", "", "x" * 65],
)
def test_invalid_board_names_are_refused(service, board_name) -> None:
    with pytest.raises(ToolError, match="invalid board name"):
        service.board_status(board_name)


def test_allow_board_limits_exposed_boards(
    board_dir, daemon, tmp_path
) -> None:
    service = _service(
        board_dir, daemon, tmp_path, allow_boards=("other",)
    )
    assert service.list_boards()["boards"] == []
    with pytest.raises(ToolError, match="not in the list of allowed boards"):
        service.board_status("lab")


# ---------------------------------------------------------------------------
# Read-only mode
# ---------------------------------------------------------------------------


def test_read_only_mode_refuses_actions_allows_reads(
    board_dir, daemon, tmp_path
) -> None:
    service = _service(board_dir, daemon, tmp_path, read_only=True)
    for call in (
        lambda: service.power_on("lab"),
        lambda: service.power_off("lab"),
        lambda: service.power_cycle("lab"),
        lambda: service.reset("lab"),
        lambda: service.set_boot_mode("emmc", "lab"),
        lambda: service.boot_and_wait("emmc", "lab"),
        lambda: service.run_workflow("power-cycle", "lab"),
    ):
        with pytest.raises(ToolError, match="read-only"):
            call()
    assert daemon.calls == []
    assert service.list_boards()["boards"]
    assert service.describe_board("lab")["board"] == "lab"
    assert service.board_status("lab")["board"] == "lab"
    assert service.audit_log()["entries"] == []


# ---------------------------------------------------------------------------
# Cooldown and locking
# ---------------------------------------------------------------------------


def test_cooldown_refuses_second_power_action(service) -> None:
    now = [100.0]
    service.clock = lambda: now[0]
    service.power_cycle("lab")
    now[0] += 1.0
    with pytest.raises(ToolError, match=r"cooldown active, 4\.0 seconds"):
        service.power_on("lab")
    now[0] += 10.0
    assert service.power_on("lab")["ok"] is True


def test_cooldown_does_not_block_non_power_tools(service) -> None:
    service.clock = lambda: 100.0
    service.power_cycle("lab")
    assert service.set_boot_mode("emmc", "lab")["ok"] is True


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_unreachable_daemon_message(board_dir, tmp_path) -> None:
    def _raise(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    settings = Settings(
        url="http://pi.local:8000",
        config_dir=board_dir,
        audit_log=tmp_path / "audit.jsonl",
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(_raise), **kwargs
        ),
    )
    service = RelayMCPService(settings)
    with pytest.raises(
        ToolError,
        match="relay daemon unreachable at http://pi.local:8000",
    ):
        service.board_status("lab")
    with pytest.raises(ToolError, match="relay daemon unreachable"):
        service.power_on("lab")


def test_execution_error_includes_step_and_hint(
    board_dir, daemon, tmp_path
) -> None:
    original = daemon.handle

    def _no_change(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/relays/5/on":
            return httpx.Response(200, json={"channel": 5, "on": False})
        return original(request)

    settings = Settings(
        config_dir=board_dir,
        audit_log=tmp_path / "audit.jsonl",
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(_no_change), **kwargs
        ),
    )
    service = RelayMCPService(settings)
    with pytest.raises(ToolError) as excinfo:
        service.power_on("lab")
    message = str(excinfo.value)
    assert "power-on" in message
    assert "expected active, got inactive" in message
    assert "hint" in message


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def test_actions_write_audit_lines_success_and_failure(service) -> None:
    service.clock = lambda: 100.0
    service.power_cycle("lab")
    with pytest.raises(ToolError):
        service.run_workflow("enter-recovery", "lab")
    entries = _audit_entries(service)
    assert len(entries) == 1  # precheck refusals happen before execution
    with pytest.raises(ToolError):
        service.power_on("lab")  # cooldown refusal, no audit entry
    service.clock = lambda: 1000.0
    service.power_on("lab")
    entries = _audit_entries(service)
    assert len(entries) == 2
    assert entries[0]["tool"] == "power_cycle"
    assert entries[0]["ok"] is True
    assert entries[0]["steps"] == ["cut-power", "settle", "restore-power"]
    newest_first = service.audit_log(limit=1)["entries"]
    assert newest_first[0]["tool"] == "power_on"


def test_failed_action_writes_audit_line(board_dir, tmp_path) -> None:
    def _raise(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    settings = Settings(
        config_dir=board_dir,
        audit_log=tmp_path / "audit.jsonl",
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(_raise), **kwargs
        ),
    )
    service = RelayMCPService(settings)
    with pytest.raises(ToolError):
        service.power_on("lab")
    entries = _audit_entries(service)
    assert len(entries) == 1
    assert entries[0]["ok"] is False
    assert entries[0]["error"]


def test_api_token_sent_but_never_logged(
    board_dir, daemon, tmp_path
) -> None:
    token = "super-secret-token"
    service = _service(board_dir, daemon, tmp_path, api_token=token)
    result = service.power_cycle("lab")
    assert daemon.requests
    for request in daemon.requests:
        assert request.headers["Authorization"] == "Bearer " + token
    assert token not in json.dumps(result)
    assert token not in service.audit.path.read_text()
    assert token not in json.dumps(service.audit_log()["entries"])


def test_audit_log_limit_is_clamped(service) -> None:
    for index in range(5):
        service.audit.record(
            board="lab", tool="power_on", arguments={}, ok=True
        )
    assert len(service.audit_log(limit=0)["entries"]) == 1
    assert len(service.audit_log(limit=3)["entries"]) == 3
    assert len(service.audit_log(limit=9999)["entries"]) == 5


# ---------------------------------------------------------------------------
# MCP server wiring
# ---------------------------------------------------------------------------


def test_create_server_exposes_expected_tools_and_prompts(
    board_dir, daemon, tmp_path, anyio_backend="asyncio"
) -> None:
    import anyio

    server = create_server(
        Settings(
            config_dir=board_dir,
            audit_log=tmp_path / "audit.jsonl",
            client_factory=lambda **kwargs: httpx.Client(
                transport=daemon.transport(), **kwargs
            ),
        )
    )

    async def _collect():
        tools = await server.list_tools()
        prompts = await server.list_prompts()
        return tools, prompts

    tools, prompts = anyio.run(_collect)
    tool_names = {tool.name for tool in tools}
    assert tool_names == {
        "list_boards",
        "describe_board",
        "board_status",
        "power_on",
        "power_off",
        "power_cycle",
        "reset",
        "set_boot_mode",
        "boot_and_wait",
        "run_workflow",
        "audit_log",
        "list_boots",
        "console_log",
        "boot_report",
        "wait_for_console",
        "console_state",
        "dut_ip_address",
        "list_console_actions",
        "run_console_action",
    }
    forbidden = {"on", "off", "toggle", "press", "all_on", "all_off"}
    assert not tool_names & forbidden
    for tool in tools:
        schema = getattr(
            tool, "input_schema", getattr(tool, "inputSchema", None)
        )
        assert "force" not in ((schema or {}).get("properties") or {})
    by_name = {tool.name: tool for tool in tools}

    def _hint(tool, snake, camel):
        annotations = tool.annotations
        return getattr(
            annotations, snake, getattr(annotations, camel, None)
        )

    assert _hint(by_name["board_status"], "read_only_hint", "readOnlyHint")
    assert _hint(by_name["power_off"], "destructive_hint", "destructiveHint")
    assert {prompt.name for prompt in prompts} == {
        "smoke_test",
        "power_cycle_and_check",
        "boot_from",
        "check_boot",
        "diagnose_unreachable",
    }


# ---------------------------------------------------------------------------
# End to end over stdio
# ---------------------------------------------------------------------------


async def test_stdio_end_to_end(board_dir, tmp_path):
    uvicorn = pytest.importorskip("uvicorn")
    import socket
    import threading
    import time as _time
    from unittest.mock import MagicMock

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    import relay_tools.api as api

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    state = {channel: False for channel in range(1, 9)}
    board = MagicMock()
    board.num_channels = 8
    board.is_on.side_effect = lambda channel: state[channel]
    board.get_state.side_effect = lambda: dict(state)
    board.turn_on.side_effect = lambda channel: state.__setitem__(
        channel, True
    )
    board.turn_off.side_effect = lambda channel: state.__setitem__(
        channel, False
    )

    api._board = board
    config = uvicorn.Config(
        api.app,
        host="127.0.0.1",
        port=port,
        lifespan="off",
        log_level="error",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = _time.time() + 10
        while not server.started:
            if _time.time() > deadline:
                pytest.skip("uvicorn did not start in time")
            _time.sleep(0.05)

        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "relay_tools.mcp.server",
                "--url",
                f"http://127.0.0.1:{port}",
                "--config-dir",
                str(board_dir),
                "--audit-log",
                str(tmp_path / "audit.jsonl"),
            ],
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                tool_names = {tool.name for tool in tools.tools}
                assert "board_status" in tool_names
                assert "power_cycle" in tool_names
                result = await session.call_tool(
                    "board_status", {"board": "lab"}
                )
                is_error = getattr(
                    result, "is_error", getattr(result, "isError", None)
                )
                assert is_error is False
                payload = json.loads(result.content[0].text)
                assert payload["board"] == "lab"
                assert "emmc" in payload["matching_boot_modes"]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        api._board = None
