"""Tests for the relay CLI (REST-based channel commands + daemon serve)."""

from __future__ import annotations

import logging
import os
from unittest.mock import patch

import httpx
import pytest
from click.testing import CliRunner

from relay_tools.cli import _DEFAULT_URL, cli
from relay_tools.client import ChannelState, RelayClient, RelayConnectionError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NUM_CHANNELS = 8


def _board_state(state: dict[int, bool] | None = None) -> dict:
    if state is None:
        state = {ch: False for ch in range(1, NUM_CHANNELS + 1)}
    return {
        "channels": [
            {"channel": ch, "on": active} for ch, active in sorted(state.items())
        ]
    }


def _channel_state(channel: int, on: bool) -> dict:
    return {"channel": channel, "on": on}


class _MockTransport(httpx.MockTransport):
    def __init__(self, routes: dict[tuple[str, str], httpx.Response]):
        self._routes = routes

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key in self._routes:
            return self._routes[key]
        return httpx.Response(404, json={"detail": "not found"})


def _make_transport(routes: dict[tuple[str, str], httpx.Response]) -> _MockTransport:
    return _MockTransport(routes)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# Helpers used in tests
# ---------------------------------------------------------------------------


def _run(
    runner: CliRunner,
    transport: httpx.MockTransport,
    *args: str,
    url: str = _DEFAULT_URL,
):
    """Invoke cli with a mocked httpx transport."""
    with patch("relay_tools.cli._client") as mock_client_factory:
        client = RelayClient(
            url,
            client_factory=lambda **kwargs: httpx.Client(
                transport=transport,
                **kwargs,
            ),
        )
        mock_client_factory.return_value.__enter__ = lambda s: client
        mock_client_factory.return_value.__exit__ = lambda s, *a: client.close()
        return runner.invoke(cli, list(args))


# ---------------------------------------------------------------------------
# Tests – channel commands (REST)
# ---------------------------------------------------------------------------


class TestCLI:
    def test_on_command(self, runner: CliRunner) -> None:
        transport = _make_transport(
            {
                ("POST", "/relays/1/on"): httpx.Response(
                    200, json=_channel_state(1, True)
                )
            }
        )
        result = _run(runner, transport, "on", "1")
        assert result.exit_code == 0
        assert "Channel 1: ON" in result.output

    def test_off_command(self, runner: CliRunner) -> None:
        transport = _make_transport(
            {
                ("POST", "/relays/2/off"): httpx.Response(
                    200, json=_channel_state(2, False)
                )
            }
        )
        result = _run(runner, transport, "off", "2")
        assert result.exit_code == 0
        assert "Channel 2: OFF" in result.output

    def test_toggle_on_to_off(self, runner: CliRunner) -> None:
        transport = _make_transport(
            {
                ("POST", "/relays/3/toggle"): httpx.Response(
                    200, json=_channel_state(3, False)
                )
            }
        )
        result = _run(runner, transport, "toggle", "3")
        assert result.exit_code == 0
        assert "Channel 3: OFF" in result.output

    def test_toggle_off_to_on(self, runner: CliRunner) -> None:
        transport = _make_transport(
            {
                ("POST", "/relays/4/toggle"): httpx.Response(
                    200, json=_channel_state(4, True)
                )
            }
        )
        result = _run(runner, transport, "toggle", "4")
        assert result.exit_code == 0
        assert "Channel 4: ON" in result.output

    def test_press_command(self, runner: CliRunner) -> None:
        transport = _make_transport(
            {
                ("POST", "/relays/5/press"): httpx.Response(
                    200, json=_channel_state(5, False)
                )
            }
        )
        result = _run(runner, transport, "press", "5")
        assert result.exit_code == 0
        assert "Channel 5: PRESSED" in result.output

    def test_press_command_with_custom_duration(self, runner: CliRunner) -> None:
        with patch("relay_tools.cli._client") as mock_factory:
            mock_client = mock_factory.return_value.__enter__.return_value
            mock_factory.return_value.__exit__ = lambda s, *a: None
            mock_client.press.return_value = ChannelState(channel=6, on=False)
            result = runner.invoke(cli, ["press", "6", "--duration", "0.5"])
        assert result.exit_code == 0
        assert "Channel 6: PRESSED" in result.output
        mock_client.press.assert_called_once_with(6, 0.5)

    def test_press_rejects_zero_duration(self, runner: CliRunner) -> None:
        with patch("relay_tools.cli._client"):
            result = runner.invoke(cli, ["press", "1", "--duration", "0"])
        assert result.exit_code != 0
        assert "x>=0.01" in result.output

    def test_status_command(self, runner: CliRunner) -> None:
        transport = _make_transport(
            {("GET", "/relays"): httpx.Response(200, json=_board_state())}
        )
        result = _run(runner, transport, "status")
        assert result.exit_code == 0
        for ch in range(1, NUM_CHANNELS + 1):
            assert str(ch) in result.output

    def test_all_on_command(self, runner: CliRunner) -> None:
        state = {ch: True for ch in range(1, NUM_CHANNELS + 1)}
        transport = _make_transport(
            {("POST", "/relays/on"): httpx.Response(200, json=_board_state(state))}
        )
        result = _run(runner, transport, "all-on")
        assert result.exit_code == 0
        assert "All channels: ON" in result.output

    def test_all_off_command(self, runner: CliRunner) -> None:
        transport = _make_transport(
            {("POST", "/relays/off"): httpx.Response(200, json=_board_state())}
        )
        result = _run(runner, transport, "all-off")
        assert result.exit_code == 0
        assert "All channels: OFF" in result.output

    def test_connection_error_shows_message(self, runner: CliRunner) -> None:
        with patch("relay_tools.cli._client") as mock_factory:
            mock_client = mock_factory.return_value.__enter__.return_value
            mock_factory.return_value.__exit__ = lambda s, *a: None
            mock_client.on.side_effect = RelayConnectionError(_DEFAULT_URL)
            result = runner.invoke(cli, ["on", "1"])
        assert result.exit_code != 0
        assert "daemon" in result.output.lower() or "connect" in result.output.lower()

    def test_default_url_is_localhost_8000(self, runner: CliRunner) -> None:
        with patch("relay_tools.cli._client") as mock_factory:
            client = RelayClient(
                _DEFAULT_URL,
                client_factory=lambda **kwargs: httpx.Client(
                    transport=_make_transport(
                        {("GET", "/relays"): httpx.Response(200, json=_board_state())}
                    ),
                    **kwargs,
                ),
            )
            mock_factory.return_value.__enter__ = lambda s: client
            mock_factory.return_value.__exit__ = lambda s, *a: client.close()
            runner.invoke(cli, ["status"])
        mock_factory.assert_called_once_with(_DEFAULT_URL)

    def test_custom_url_option(self, runner: CliRunner) -> None:
        custom_url = "http://pi.local:9000"
        with patch("relay_tools.cli._client") as mock_factory:
            client = RelayClient(
                custom_url,
                client_factory=lambda **kwargs: httpx.Client(
                    transport=_make_transport(
                        {("GET", "/relays"): httpx.Response(200, json=_board_state())}
                    ),
                    **kwargs,
                ),
            )
            mock_factory.return_value.__enter__ = lambda s: client
            mock_factory.return_value.__exit__ = lambda s, *a: client.close()
            runner.invoke(cli, ["--url", custom_url, "status"])
        mock_factory.assert_called_once_with(custom_url)

    def test_verbose_flag_enables_debug_logging(self, runner: CliRunner) -> None:
        transport = _make_transport(
            {("GET", "/relays"): httpx.Response(200, json=_board_state())}
        )
        root_logger = logging.getLogger()
        original_level = root_logger.level
        original_handlers = root_logger.handlers[:]
        root_logger.handlers.clear()
        try:
            result = _run(runner, transport, "--verbose", "status")
            assert result.exit_code == 0
            assert root_logger.level == logging.DEBUG
        finally:
            root_logger.setLevel(original_level)
            root_logger.handlers = original_handlers

    def test_verbose_short_flag(self, runner: CliRunner) -> None:
        transport = _make_transport(
            {("GET", "/relays"): httpx.Response(200, json=_board_state())}
        )
        root_logger = logging.getLogger()
        original_level = root_logger.level
        original_handlers = root_logger.handlers[:]
        root_logger.handlers.clear()
        try:
            result = _run(runner, transport, "-v", "status")
            assert result.exit_code == 0
            assert root_logger.level == logging.DEBUG
        finally:
            root_logger.setLevel(original_level)
            root_logger.handlers = original_handlers


# ---------------------------------------------------------------------------
# Tests – relay serve
# ---------------------------------------------------------------------------


class TestServeCommand:
    def test_serve_default_host_and_port(self, runner: CliRunner) -> None:
        """relay serve starts uvicorn on 0.0.0.0:8000 by default."""
        with patch("relay_tools.cli.uvicorn") as mock_uvicorn:
            result = runner.invoke(cli, ["serve"])
        assert result.exit_code == 0
        mock_uvicorn.run.assert_called_once_with(
            "relay_tools.api:app", host="0.0.0.0", port=8000
        )

    def test_serve_custom_host_and_port(self, runner: CliRunner) -> None:
        """--host and --port are forwarded to uvicorn.run."""
        with patch("relay_tools.cli.uvicorn") as mock_uvicorn:
            result = runner.invoke(
                cli, ["serve", "--host", "127.0.0.1", "--port", "9000"]
            )
        assert result.exit_code == 0
        mock_uvicorn.run.assert_called_once_with(
            "relay_tools.api:app", host="127.0.0.1", port=9000
        )

    def test_serve_sets_relay_driver_env_auto(
        self, runner: CliRunner, monkeypatch
    ) -> None:
        """Default driver 'auto' is written to RELAY_DRIVER env var."""
        monkeypatch.delenv("RELAY_DRIVER", raising=False)
        with patch("relay_tools.cli.uvicorn"):
            runner.invoke(cli, ["serve"])
        assert os.environ.get("RELAY_DRIVER") == "auto"

    def test_serve_sets_relay_driver_env_explicit(self, runner: CliRunner) -> None:
        """Explicit --driver value is written to RELAY_DRIVER env var."""
        with patch("relay_tools.cli.uvicorn"):
            runner.invoke(cli, ["serve", "--driver", "rpigpio"])
        assert os.environ.get("RELAY_DRIVER") == "rpigpio"

    def test_serve_config_option_sets_relay_config_env(
        self, runner: CliRunner, monkeypatch
    ) -> None:
        """--config sets the RELAY_CONFIG environment variable."""
        monkeypatch.delenv("RELAY_CONFIG", raising=False)
        with patch("relay_tools.cli.uvicorn"):
            result = runner.invoke(
                cli, ["serve", "--config", "/etc/relay/channels.yaml"]
            )
        assert result.exit_code == 0
        assert os.environ.get("RELAY_CONFIG") == "/etc/relay/channels.yaml"

    def test_serve_no_config_option_leaves_relay_config_env_unchanged(
        self, runner: CliRunner, monkeypatch
    ) -> None:
        """Without --config the RELAY_CONFIG env var is not modified."""
        monkeypatch.delenv("RELAY_CONFIG", raising=False)
        with patch("relay_tools.cli.uvicorn"):
            runner.invoke(cli, ["serve"])
        assert os.environ.get("RELAY_CONFIG") is None
