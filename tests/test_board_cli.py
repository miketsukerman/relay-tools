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


def _write_profile(tmp_path):
    path = tmp_path / "board.yaml"
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
""",
    )
    return path


def _run(runner: CliRunner, transport: _RecordingTransport, profile_path, *args: str):
    with patch("relay_tools.board_cli._client") as mock_client_factory:
        client = RelayClient(
            DEFAULT_URL,
            client_factory=lambda **kwargs: httpx.Client(
                transport=transport,
                **kwargs,
            ),
        )
        mock_client_factory.return_value = client
        return runner.invoke(
            board_cli,
            ["--config", str(profile_path), *args],
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
