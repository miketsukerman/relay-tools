"""Tests for the board CLI and relay translation."""

from __future__ import annotations

import re
from unittest.mock import patch

import httpx
from click.testing import CliRunner

from relay_tools.board_cli import board_cli
from relay_tools.client import DEFAULT_URL, RelayClient


class _RecordingTransport(httpx.MockTransport):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.state = {channel: False for channel in range(1, 9)}

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path == "/relays":
            return httpx.Response(
                200,
                json={
                    "channels": [
                        {"channel": channel, "on": state}
                        for channel, state in sorted(self.state.items())
                    ]
                },
            )
        if request.method == "GET":
            channel = int(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(
                200,
                json={"channel": channel, "on": self.state[channel]},
            )
        match = re.fullmatch(r"/relays/(\d+)/(on|off|press)", request.url.path)
        if not match:
            return httpx.Response(404, json={"detail": "not found"})
        channel = int(match.group(1))
        action = match.group(2)
        if action == "on":
            self.state[channel] = True
            return httpx.Response(200, json={"channel": channel, "on": True})
        if action == "off":
            self.state[channel] = False
            return httpx.Response(200, json={"channel": channel, "on": False})
        self.state[channel] = False
        return httpx.Response(200, json={"channel": channel, "on": False})


def _write_profile(tmp_path, name: str = "board.yaml"):
    path = tmp_path / name
    path.write_text(
        """
name: rom2820
defaults:
  power_switch: general_power_input
signals:
switches:
  sw1003: {channel: 1}
  sw1002: {channel: 2}
  sw1001_2: {channel: 3}
  sw1001_1: {channel: 4}
  general_power_input: {channel: 5}
timings:
  settle_delay: 0.5
  boot_wait: 1.5
boot_modes:
  emmc:
    switches:
      sw1003: off
      sw1002: off
  recovery:
    risky: true
    switches:
      sw1003: on
      sw1002: on
workflows:
  flash-internal-memory:
    - action: set
      switch: sw1003
      state: on
""",
    )
    return path


def _run(
    runner: CliRunner,
    transport: _RecordingTransport,
    profile_path,
    *args: str,
    include_config_option: bool = True,
    env: dict[str, str] | None = None,
):
    with patch("relay_tools.board_cli._client") as mock_client_factory:
        client = RelayClient(
            DEFAULT_URL,
            client_factory=lambda **kwargs: httpx.Client(
                transport=transport,
                **kwargs,
            ),
        )
        mock_client_factory.return_value = client
        cli_args = list(args)
        if include_config_option:
            cli_args = ["--config", str(profile_path), *cli_args]
        return runner.invoke(
            board_cli,
            cli_args,
            env=env,
        )


def test_status_reports_matching_boot_mode(tmp_path) -> None:
    runner = CliRunner()
    transport = _RecordingTransport()
    profile = _write_profile(tmp_path)

    result = _run(runner, transport, profile, "status")

    assert result.exit_code == 0
    assert "Matching boot modes: emmc" in result.output


def test_set_boot_mode_translates_to_relay_api_calls(tmp_path) -> None:
    runner = CliRunner()
    transport = _RecordingTransport()
    profile = _write_profile(tmp_path)

    result = _run(
        runner,
        transport,
        profile,
        "set-boot-mode",
        "recovery",
        "--force",
        "--no-verify",
    )

    assert result.exit_code == 0
    assert transport.calls[:3] == [
        ("POST", "/relays/1/on"),
        ("POST", "/relays/2/on"),
        ("GET", "/relays"),
    ]


def test_boot_and_wait_runs_boot_mode_then_power_sequence(
    tmp_path,
    monkeypatch,
) -> None:
    runner = CliRunner()
    transport = _RecordingTransport()
    profile = _write_profile(tmp_path)
    delays = []
    monkeypatch.setattr(
        "relay_tools.board.time.sleep",
        lambda duration: delays.append(duration),
    )

    result = _run(
        runner,
        transport,
        profile,
        "boot-and-wait",
        "emmc",
        "--no-verify",
    )

    assert result.exit_code == 0
    assert transport.calls[:4] == [
        ("POST", "/relays/1/off"),
        ("POST", "/relays/2/off"),
        ("POST", "/relays/5/off"),
        ("POST", "/relays/5/on"),
    ]
    assert delays == [0.5, 1.5]


def test_run_workflow_executes_named_workflow(tmp_path) -> None:
    runner = CliRunner()
    transport = _RecordingTransport()
    profile = _write_profile(tmp_path)

    result = _run(
        runner,
        transport,
        profile,
        "run-workflow",
        "flash-internal-memory",
        "--no-verify",
    )

    assert result.exit_code == 0
    assert transport.calls[:1] == [("POST", "/relays/1/on")]


def test_flash_internal_memory_runs_standard_workflow(tmp_path) -> None:
    runner = CliRunner()
    transport = _RecordingTransport()
    profile = _write_profile(tmp_path)

    result = _run(
        runner,
        transport,
        profile,
        "flash-internal-memory",
        "--no-verify",
    )

    assert result.exit_code == 0
    assert transport.calls[:1] == [("POST", "/relays/1/on")]


def test_config_name_resolves_profile_from_default_directory(tmp_path) -> None:
    runner = CliRunner()
    transport = _RecordingTransport()
    profile = _write_profile(tmp_path, "lab.yaml")

    with patch(
        "relay_tools.board_cli.DEFAULT_BOARD_CONFIG_DIR",
        tmp_path,
    ):
        result = _run(
            runner,
            transport,
            profile,
            "lab",
            "status",
            include_config_option=False,
        )

    assert result.exit_code == 0
    assert "Matching boot modes: emmc" in result.output


def test_config_name_and_config_option_are_mutually_exclusive(tmp_path) -> None:
    runner = CliRunner()
    transport = _RecordingTransport()
    profile = _write_profile(tmp_path)

    with patch("relay_tools.board_cli._client") as mock_client_factory:
        client = RelayClient(
            DEFAULT_URL,
            client_factory=lambda **kwargs: httpx.Client(
                transport=transport,
                **kwargs,
            ),
        )
        mock_client_factory.return_value = client
        result = runner.invoke(
            board_cli,
            ["rom2820", "--config", str(profile), "status"],
        )

    assert result.exit_code != 0
    assert (
        "Specify either board config name or --config path, not both."
        in result.output
    )


def test_relay_board_default_env_name_resolves_from_default_directory(tmp_path) -> None:
    runner = CliRunner()
    transport = _RecordingTransport()
    _write_profile(tmp_path, "lab.yaml")

    with patch(
        "relay_tools.board_cli.DEFAULT_BOARD_CONFIG_DIR",
        tmp_path,
    ):
        result = _run(
            runner,
            transport,
            tmp_path / "ignored.yaml",
            "status",
            include_config_option=False,
            env={"RELAY_BOARD_DEFAULT": "lab"},
        )

    assert result.exit_code == 0
    assert "Matching boot modes: emmc" in result.output


def test_relay_board_default_env_accepts_absolute_path(tmp_path) -> None:
    runner = CliRunner()
    transport = _RecordingTransport()
    profile = _write_profile(tmp_path, "custom.yaml")

    result = _run(
        runner,
        transport,
        tmp_path / "ignored.yaml",
        "status",
        include_config_option=False,
        env={"RELAY_BOARD_DEFAULT": str(profile)},
    )

    assert result.exit_code == 0
    assert "Matching boot modes: emmc" in result.output


def test_config_name_wins_over_environment_default(tmp_path) -> None:
    runner = CliRunner()
    transport = _RecordingTransport()
    profile = _write_profile(tmp_path, "lab.yaml")

    with patch(
        "relay_tools.board_cli.DEFAULT_BOARD_CONFIG_DIR",
        tmp_path,
    ):
        result = _run(
            runner,
            transport,
            profile,
            "lab",
            "status",
            include_config_option=False,
            env={"RELAY_BOARD_CONFIG": "/does/not/exist.yaml"},
        )

    assert result.exit_code == 0
    assert "Matching boot modes: emmc" in result.output


def test_config_option_wins_over_environment_default(tmp_path) -> None:
    runner = CliRunner()
    transport = _RecordingTransport()
    profile = _write_profile(tmp_path)

    result = _run(
        runner,
        transport,
        profile,
        "status",
        env={"RELAY_BOARD_CONFIG": "/does/not/exist.yaml"},
    )

    assert result.exit_code == 0
    assert "Matching boot modes: emmc" in result.output


def test_relay_board_config_env_used_when_no_cli_selection(tmp_path) -> None:
    runner = CliRunner()
    transport = _RecordingTransport()
    profile = _write_profile(tmp_path, "from-env.yaml")

    result = _run(
        runner,
        transport,
        tmp_path / "ignored.yaml",
        "status",
        include_config_option=False,
        env={"RELAY_BOARD_CONFIG": str(profile)},
    )

    assert result.exit_code == 0
    assert "Matching boot modes: emmc" in result.output
