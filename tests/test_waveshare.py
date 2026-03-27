"""Tests for the Waveshare relay board driver (GPIO mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NUM_CHANNELS = 8


def _make_output_device(
    pin: int, *, active_high: bool = False, initial_value: bool = False
):
    """Return a MagicMock that simulates gpiozero.OutputDevice."""
    dev = MagicMock()
    dev.value = 1 if initial_value else 0

    def _on() -> None:
        dev.value = 1

    def _off() -> None:
        dev.value = 0

    dev.on.side_effect = _on
    dev.off.side_effect = _off
    return dev


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def board():
    """Return a WaveshareRelayBoard with gpiozero.OutputDevice mocked."""
    with patch("relay_tools.waveshare.OutputDevice", side_effect=_make_output_device), \
         patch("relay_tools.waveshare._HAS_GPIOZERO", True):
        from relay_tools.waveshare import WaveshareRelayBoard
        b = WaveshareRelayBoard()
    return b


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWaveshareRelayBoard:
    def test_num_channels(self, board) -> None:
        assert board.num_channels == NUM_CHANNELS

    def test_initial_state_all_off(self, board) -> None:
        for ch in range(1, NUM_CHANNELS + 1):
            assert board.is_on(ch) is False

    def test_turn_on(self, board) -> None:
        board.turn_on(1)
        assert board.is_on(1) is True

    def test_turn_off(self, board) -> None:
        board.turn_on(1)
        board.turn_off(1)
        assert board.is_on(1) is False

    def test_get_state(self, board) -> None:
        board.turn_on(3)
        state = board.get_state()
        assert state[3] is True
        assert state[1] is False

    def test_turn_on_all(self, board) -> None:
        board.turn_on_all()
        assert all(board.get_state().values())

    def test_turn_off_all(self, board) -> None:
        board.turn_on_all()
        board.turn_off_all()
        assert not any(board.get_state().values())

    def test_invalid_channel_too_low(self, board) -> None:
        with pytest.raises(ValueError):
            board.turn_on(0)

    def test_invalid_channel_too_high(self, board) -> None:
        with pytest.raises(ValueError):
            board.turn_on(NUM_CHANNELS + 1)

    def test_context_manager_calls_close(self, board) -> None:
        board.close()  # verify it does not raise
        for relay in board._relays:
            relay.close.assert_called_once()
