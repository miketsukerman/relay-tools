"""Tests for the Click CLI."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from relay_tools.cli import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NUM_CHANNELS = 8


def _make_board(initial_state: dict[int, bool] | None = None):
    """Return a MagicMock that mimics WaveshareRelayBoard."""
    board = MagicMock()
    state: dict[int, bool] = (
        initial_state if initial_state is not None
        else {ch: False for ch in range(1, NUM_CHANNELS + 1)}
    )

    board.__enter__ = MagicMock(return_value=board)
    board.__exit__ = MagicMock(return_value=False)
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
