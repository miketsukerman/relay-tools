"""Tests for the Click CLI."""

from __future__ import annotations

import logging
import os
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from relay_tools.cli import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NUM_CHANNELS = 8


def _make_board(initial_state: dict[int, bool] | None = None):
    """Return a MagicMock that mimics AbstractRelayBoard."""
    board = MagicMock()
    state: dict[int, bool] = (
        initial_state if initial_state is not None
        else {ch: False for ch in range(1, NUM_CHANNELS + 1)}
    )

    board.num_channels = NUM_CHANNELS
    board.is_on.side_effect = lambda ch: state[ch]
    board.get_state.return_value = state

    def _turn_on(ch):
        state[ch] = True

    def _turn_off(ch):
        state[ch] = False

    board.turn_on.side_effect = _turn_on
    board.turn_off.side_effect = _turn_off
    board.turn_on_all.side_effect = lambda: state.update({ch: True for ch in state})
    board.turn_off_all.side_effect = lambda: state.update({ch: False for ch in state})
    return board


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCLI:
    def _run(self, runner, board, *args):
        with patch("relay_tools.cli._get_board", return_value=board):
            return runner.invoke(cli, list(args))

    def test_on_command(self, runner) -> None:
        board = _make_board()
        result = self._run(runner, board, "on", "1")
        assert result.exit_code == 0
        assert "ON" in result.output
        board.turn_on.assert_called_once_with(1)

    def test_off_command(self, runner) -> None:
        board = _make_board({ch: True for ch in range(1, NUM_CHANNELS + 1)})
        result = self._run(runner, board, "off", "2")
        assert result.exit_code == 0
        assert "OFF" in result.output
        board.turn_off.assert_called_once_with(2)

    def test_toggle_on_to_off(self, runner) -> None:
        board = _make_board({ch: (ch == 3) for ch in range(1, NUM_CHANNELS + 1)})
        result = self._run(runner, board, "toggle", "3")
        assert result.exit_code == 0
        assert "OFF" in result.output

    def test_toggle_off_to_on(self, runner) -> None:
        board = _make_board()
        result = self._run(runner, board, "toggle", "4")
        assert result.exit_code == 0
        assert "ON" in result.output

    def test_press_command(self, runner) -> None:
        board = _make_board()
        with patch("relay_tools.cli.time.sleep") as mock_sleep:
            result = self._run(runner, board, "press", "5")
        assert result.exit_code == 0
        assert "PRESSED" in result.output
        board.turn_on.assert_called_once_with(5)
        board.turn_off.assert_called_once_with(5)
        mock_sleep.assert_called_once_with(0.2)

    def test_status_command(self, runner) -> None:
        board = _make_board()
        result = self._run(runner, board, "status")
        assert result.exit_code == 0
        # All 8 channels should appear in output
        for ch in range(1, NUM_CHANNELS + 1):
            assert str(ch) in result.output

    def test_all_on_command(self, runner) -> None:
        board = _make_board()
        result = self._run(runner, board, "all-on")
        assert result.exit_code == 0
        assert "ON" in result.output
        board.turn_on_all.assert_called_once()

    def test_all_off_command(self, runner) -> None:
        board = _make_board({ch: True for ch in range(1, NUM_CHANNELS + 1)})
        result = self._run(runner, board, "all-off")
        assert result.exit_code == 0
        assert "OFF" in result.output
        board.turn_off_all.assert_called_once()

    def test_driver_default_is_auto(self, runner) -> None:
        """Default driver should be 'auto' (auto-detect)."""
        with patch(
            "relay_tools.cli._get_board", return_value=_make_board()
        ) as mock_get:
            runner.invoke(cli, ["on", "1"])
        mock_get.assert_called_once_with("auto")

    def test_driver_rpigpio_option(self, runner) -> None:
        """--driver rpigpio should pass 'rpigpio' to _get_board."""
        with patch(
            "relay_tools.cli._get_board", return_value=_make_board()
        ) as mock_get:
            runner.invoke(cli, ["--driver", "rpigpio", "on", "1"])
        mock_get.assert_called_once_with("rpigpio")

    def test_driver_gpiozero_option(self, runner) -> None:
        """--driver gpiozero should pass 'gpiozero' to _get_board."""
        with patch(
            "relay_tools.cli._get_board", return_value=_make_board()
        ) as mock_get:
            runner.invoke(cli, ["--driver", "gpiozero", "on", "1"])
        mock_get.assert_called_once_with("gpiozero")

    def test_board_not_closed_after_on(self, runner) -> None:
        """close() must NOT be called after on – relay must stay energized."""
        board = _make_board()
        self._run(runner, board, "on", "1")
        board.close.assert_not_called()

    def test_board_not_closed_after_off(self, runner) -> None:
        """close() must NOT be called after off – relay must stay de-energized."""
        board = _make_board({ch: True for ch in range(1, NUM_CHANNELS + 1)})
        self._run(runner, board, "off", "1")
        board.close.assert_not_called()

    def test_verbose_flag_enables_debug_logging(self, runner) -> None:
        board = _make_board()
        # Reset logging state so basicConfig takes effect inside the runner
        root_logger = logging.getLogger()
        original_level = root_logger.level
        original_handlers = root_logger.handlers[:]
        root_logger.handlers.clear()
        try:
            with patch("relay_tools.cli._get_board", return_value=board):
                result = runner.invoke(cli, ["--verbose", "on", "1"])
            assert result.exit_code == 0
            assert root_logger.level == logging.DEBUG
        finally:
            root_logger.setLevel(original_level)
            root_logger.handlers = original_handlers

    def test_no_verbose_flag_uses_warning_logging(self, runner) -> None:
        board = _make_board()
        root_logger = logging.getLogger()
        original_level = root_logger.level
        original_handlers = root_logger.handlers[:]
        root_logger.handlers.clear()
        try:
            with patch("relay_tools.cli._get_board", return_value=board):
                result = runner.invoke(cli, ["on", "1"])
            assert result.exit_code == 0
            assert root_logger.level == logging.WARNING
        finally:
            root_logger.setLevel(original_level)
            root_logger.handlers = original_handlers

    def test_verbose_short_flag(self, runner) -> None:
        board = _make_board()
        root_logger = logging.getLogger()
        original_level = root_logger.level
        original_handlers = root_logger.handlers[:]
        root_logger.handlers.clear()
        try:
            with patch("relay_tools.cli._get_board", return_value=board):
                result = runner.invoke(cli, ["-v", "on", "1"])
            assert result.exit_code == 0
            assert root_logger.level == logging.DEBUG
        finally:
            root_logger.setLevel(original_level)
            root_logger.handlers = original_handlers


# ---------------------------------------------------------------------------
# _get_board unit tests (auto-detection / error handling)
# ---------------------------------------------------------------------------


class TestGetBoard:
    """Test auto-detection and error handling in _get_board."""

    def test_auto_uses_rpigpio_when_available(self) -> None:
        from relay_tools.cli import _get_board
        mock_board = MagicMock()
        with patch(
            "relay_tools.cli.WaveshareRelayBoardRPiGPIO", return_value=mock_board
        ):
            board = _get_board("auto")
        assert board is mock_board

    def test_auto_falls_back_to_gpiozero_when_rpigpio_missing(self) -> None:
        from relay_tools.cli import _get_board
        mock_board = MagicMock()
        with (
            patch(
                "relay_tools.cli.WaveshareRelayBoardRPiGPIO",
                side_effect=ImportError("RPi.GPIO not installed"),
            ),
            patch("relay_tools.cli.WaveshareRelayBoard", return_value=mock_board),
        ):
            board = _get_board("auto")
        assert board is mock_board

    def test_explicit_rpigpio_raises_click_exception_when_missing(self) -> None:
        import click

        from relay_tools.cli import _get_board
        with patch(
            "relay_tools.cli.WaveshareRelayBoardRPiGPIO",
            side_effect=ImportError("RPi.GPIO not installed"),
        ):
            with pytest.raises(click.ClickException):
                _get_board("rpigpio")

    def test_explicit_gpiozero_raises_click_exception_when_missing(self) -> None:
        import click

        from relay_tools.cli import _get_board
        with patch(
            "relay_tools.cli.WaveshareRelayBoard",
            side_effect=ImportError("gpiozero not installed"),
        ):
            with pytest.raises(click.ClickException):
                _get_board("gpiozero")

    def test_auto_raises_click_exception_when_both_missing(self) -> None:
        import click

        from relay_tools.cli import _get_board
        with (
            patch(
                "relay_tools.cli.WaveshareRelayBoardRPiGPIO",
                side_effect=ImportError("RPi.GPIO not installed"),
            ),
            patch(
                "relay_tools.cli.WaveshareRelayBoard",
                side_effect=ImportError("gpiozero not installed"),
            ),
        ):
            with pytest.raises(click.ClickException):
                _get_board("auto")


# ---------------------------------------------------------------------------
# relay serve tests
# ---------------------------------------------------------------------------

class TestServeCommand:
    def test_serve_default_host_and_port(self, runner) -> None:
        """relay serve starts uvicorn on 0.0.0.0:8000 by default."""
        with patch("relay_tools.cli.uvicorn") as mock_uvicorn:
            result = runner.invoke(cli, ["serve"])
        assert result.exit_code == 0
        mock_uvicorn.run.assert_called_once_with(
            "relay_tools.api:app", host="0.0.0.0", port=8000
        )

    def test_serve_custom_host_and_port(self, runner) -> None:
        """--host and --port are forwarded to uvicorn.run."""
        with patch("relay_tools.cli.uvicorn") as mock_uvicorn:
            result = runner.invoke(
                cli, ["serve", "--host", "127.0.0.1", "--port", "9000"]
            )
        assert result.exit_code == 0
        mock_uvicorn.run.assert_called_once_with(
            "relay_tools.api:app", host="127.0.0.1", port=9000
        )

    def test_serve_sets_relay_driver_env_auto(self, runner, monkeypatch) -> None:
        """Default driver 'auto' is written to RELAY_DRIVER env var."""
        monkeypatch.delenv("RELAY_DRIVER", raising=False)
        with patch("relay_tools.cli.uvicorn"):
            runner.invoke(cli, ["serve"])
        assert os.environ.get("RELAY_DRIVER") == "auto"

    def test_serve_sets_relay_driver_env_explicit(self, runner) -> None:
        """Explicit --driver value is written to RELAY_DRIVER env var."""
        with patch("relay_tools.cli.uvicorn"):
            runner.invoke(cli, ["--driver", "rpigpio", "serve"])
        assert os.environ.get("RELAY_DRIVER") == "rpigpio"

    def test_serve_config_option_sets_relay_config_env(
        self, runner, monkeypatch
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
        self, runner, monkeypatch
    ) -> None:
        """Without --config the RELAY_CONFIG env var is not modified."""
        monkeypatch.delenv("RELAY_CONFIG", raising=False)
        with patch("relay_tools.cli.uvicorn"):
            runner.invoke(cli, ["serve"])
        assert os.environ.get("RELAY_CONFIG") is None
