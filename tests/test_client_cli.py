"""Tests for the HTTP client CLI (relay-client)."""

from __future__ import annotations

import logging
from unittest.mock import patch

import httpx
import pytest
from click.testing import CliRunner

from relay_tools.client_cli import _DEFAULT_URL, client_cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NUM_CHANNELS = 8


def _board_state(state: dict[int, bool] | None = None) -> dict:
    """Build the BoardState JSON response body."""
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
    """Thin wrapper so we can register per-route handlers."""

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
    """Invoke client_cli with a mocked httpx transport."""
    with patch("relay_tools.client_cli._client") as mock_client_factory:
        # Build a real httpx.Client backed by the mock transport
        client = httpx.Client(base_url=url, transport=transport)
        mock_client_factory.return_value.__enter__ = lambda s: client
        mock_client_factory.return_value.__exit__ = lambda s, *a: None
        return runner.invoke(client_cli, list(args))


# ---------------------------------------------------------------------------
# Tests – individual channel commands
# ---------------------------------------------------------------------------


class TestClientCLIChannelCommands:
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


# ---------------------------------------------------------------------------
# Tests – error handling
# ---------------------------------------------------------------------------


class TestClientCLIErrors:
    def test_http_404_shows_error(self, runner: CliRunner) -> None:
        transport = _make_transport(
            {
                ("POST", "/relays/99/on"): httpx.Response(
                    404, json={"detail": "channel 99 out of range"}
                )
            }
        )
        result = _run(runner, transport, "on", "99")
        assert result.exit_code != 0
        assert "404" in result.output or "99" in result.output

    def test_connection_error_shows_message(self, runner: CliRunner) -> None:
        with patch("relay_tools.client_cli._client") as mock_factory:
            mock_factory.return_value.__enter__ = lambda s: (_ for _ in ()).throw(
                httpx.ConnectError("refused")
            )
            mock_factory.return_value.__exit__ = lambda s, *a: None
            result = runner.invoke(client_cli, ["on", "1"])
        assert result.exit_code != 0
        assert "daemon" in result.output.lower() or "connect" in result.output.lower()

    def test_http_503_shows_error(self, runner: CliRunner) -> None:
        transport = _make_transport(
            {
                ("GET", "/relays"): httpx.Response(
                    503, json={"detail": "board not available"}
                )
            }
        )
        result = _run(runner, transport, "status")
        assert result.exit_code != 0
        assert "503" in result.output


# ---------------------------------------------------------------------------
# Tests – options
# ---------------------------------------------------------------------------


class TestClientCLIOptions:
    def test_default_url_is_localhost_8000(self, runner: CliRunner) -> None:
        with patch("relay_tools.client_cli._client") as mock_factory:
            client = httpx.Client(
                base_url=_DEFAULT_URL,
                transport=_make_transport(
                    {("GET", "/relays"): httpx.Response(200, json=_board_state())}
                ),
            )
            mock_factory.return_value.__enter__ = lambda s: client
            mock_factory.return_value.__exit__ = lambda s, *a: None
            runner.invoke(client_cli, ["status"])
        mock_factory.assert_called_once_with(_DEFAULT_URL)

    def test_custom_url_option(self, runner: CliRunner) -> None:
        custom_url = "http://pi.local:9000"
        with patch("relay_tools.client_cli._client") as mock_factory:
            client = httpx.Client(
                base_url=custom_url,
                transport=_make_transport(
                    {("GET", "/relays"): httpx.Response(200, json=_board_state())}
                ),
            )
            mock_factory.return_value.__enter__ = lambda s: client
            mock_factory.return_value.__exit__ = lambda s, *a: None
            runner.invoke(client_cli, ["--url", custom_url, "status"])
        mock_factory.assert_called_once_with(custom_url)

    def test_url_from_env_var(self, runner: CliRunner, monkeypatch) -> None:
        monkeypatch.setenv("RELAY_API_URL", "http://env-host:7777")
        with patch("relay_tools.client_cli._client") as mock_factory:
            client = httpx.Client(
                base_url="http://env-host:7777",
                transport=_make_transport(
                    {("GET", "/relays"): httpx.Response(200, json=_board_state())}
                ),
            )
            mock_factory.return_value.__enter__ = lambda s: client
            mock_factory.return_value.__exit__ = lambda s, *a: None
            runner.invoke(client_cli, ["status"])
        mock_factory.assert_called_once_with("http://env-host:7777")

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

    def test_v_short_flag_enables_debug_logging(self, runner: CliRunner) -> None:
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
