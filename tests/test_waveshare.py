"""Tests for the Waveshare relay board driver (GPIO mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NUM_CHANNELS = 8

# BCM pin mapping mirrored from waveshare.py (index 0 unused)
_CHANNEL_PINS = (0, 5, 6, 13, 16, 19, 20, 21, 26)


def _make_output_device(
    pin: int, *, active_high: bool = False, initial_value: bool | None = None
):
    """Return a MagicMock that simulates gpiozero.OutputDevice."""
    dev = MagicMock()
    # None means "preserve current state" – in tests, treat as starting from OFF.
    dev.value = 1 if initial_value is True else 0

    def _on() -> None:
        dev.value = 1

    def _off() -> None:
        dev.value = 0

    dev.on.side_effect = _on
    dev.off.side_effect = _off
    return dev


def _make_gpio_mock():
    """Return a MagicMock that simulates RPi.GPIO.

    Pin state tracks active-LOW logic: LOW (0) = ON, HIGH (1) = OFF.
    Pins start in INPUT mode until ``GPIO.setup()`` is called.
    """
    gpio = MagicMock()
    gpio.BCM = 11
    gpio.OUT = 0
    gpio.IN = 1
    gpio.LOW = 0
    gpio.HIGH = 1

    # Per-pin output state (set by GPIO.output).
    _pin_state: dict[int, int] = {}
    # Pins that have been configured as OUTPUT by GPIO.setup.
    _pin_configured: set[int] = set()

    def _setup(pin, direction):
        _pin_configured.add(pin)

    def _output(pin, value):
        _pin_state[pin] = value

    def _input(pin):
        return _pin_state.get(pin, gpio.HIGH)  # default HIGH = de-energised

    def _gpio_function(pin):
        return gpio.OUT if pin in _pin_configured else gpio.IN

    gpio.setup.side_effect = _setup
    gpio.output.side_effect = _output
    gpio.input.side_effect = _input
    gpio.gpio_function.side_effect = _gpio_function
    return gpio


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


@pytest.fixture()
def rpi_gpio_mock():
    """Return the GPIO mock used for WaveshareRelayBoardRPiGPIO tests."""
    return _make_gpio_mock()


@pytest.fixture()
def rpi_board(rpi_gpio_mock):
    """Return a WaveshareRelayBoardRPiGPIO with RPi.GPIO mocked."""
    with patch("relay_tools.waveshare.GPIO", rpi_gpio_mock), \
         patch("relay_tools.waveshare._HAS_RPIGPIO", True):
        from relay_tools.waveshare import WaveshareRelayBoardRPiGPIO
        b = WaveshareRelayBoardRPiGPIO()
        yield b


# ---------------------------------------------------------------------------
# Tests – gpiozero backend
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


# ---------------------------------------------------------------------------
# Tests – RPi.GPIO backend
# ---------------------------------------------------------------------------


class TestWaveshareRelayBoardRPiGPIO:
    def test_num_channels(self, rpi_board) -> None:
        assert rpi_board.num_channels == NUM_CHANNELS

    def test_initial_state_all_off(self, rpi_board) -> None:
        for ch in range(1, NUM_CHANNELS + 1):
            assert rpi_board.is_on(ch) is False

    def test_turn_on(self, rpi_board, rpi_gpio_mock) -> None:
        rpi_board.turn_on(1)
        assert rpi_board.is_on(1) is True
        rpi_gpio_mock.output.assert_any_call(_CHANNEL_PINS[1], rpi_gpio_mock.LOW)

    def test_turn_off(self, rpi_board, rpi_gpio_mock) -> None:
        rpi_board.turn_on(1)
        rpi_board.turn_off(1)
        assert rpi_board.is_on(1) is False
        rpi_gpio_mock.output.assert_any_call(_CHANNEL_PINS[1], rpi_gpio_mock.HIGH)

    def test_get_state(self, rpi_board) -> None:
        rpi_board.turn_on(3)
        state = rpi_board.get_state()
        assert state[3] is True
        assert state[1] is False

    def test_turn_on_all(self, rpi_board) -> None:
        rpi_board.turn_on_all()
        assert all(rpi_board.get_state().values())

    def test_turn_off_all(self, rpi_board) -> None:
        rpi_board.turn_on_all()
        rpi_board.turn_off_all()
        assert not any(rpi_board.get_state().values())

    def test_invalid_channel_too_low(self, rpi_board) -> None:
        with pytest.raises(ValueError):
            rpi_board.turn_on(0)

    def test_invalid_channel_too_high(self, rpi_board) -> None:
        with pytest.raises(ValueError):
            rpi_board.turn_on(NUM_CHANNELS + 1)

    def test_init_sets_bcm_mode(self, rpi_gpio_mock) -> None:
        with patch("relay_tools.waveshare.GPIO", rpi_gpio_mock), \
             patch("relay_tools.waveshare._HAS_RPIGPIO", True):
            from relay_tools.waveshare import WaveshareRelayBoardRPiGPIO
            WaveshareRelayBoardRPiGPIO()
        rpi_gpio_mock.setmode.assert_called_once_with(rpi_gpio_mock.BCM)

    def test_init_configures_all_pins_as_output(self, rpi_gpio_mock) -> None:
        with patch("relay_tools.waveshare.GPIO", rpi_gpio_mock), \
             patch("relay_tools.waveshare._HAS_RPIGPIO", True):
            from relay_tools.waveshare import WaveshareRelayBoardRPiGPIO
            WaveshareRelayBoardRPiGPIO()
        setup_calls = [c[0][0] for c in rpi_gpio_mock.setup.call_args_list]
        assert sorted(setup_calls) == sorted(_CHANNEL_PINS[1:])

    def test_init_initial_state_on(self, rpi_gpio_mock) -> None:
        with patch("relay_tools.waveshare.GPIO", rpi_gpio_mock), \
             patch("relay_tools.waveshare._HAS_RPIGPIO", True):
            from relay_tools.waveshare import WaveshareRelayBoardRPiGPIO
            b = WaveshareRelayBoardRPiGPIO(initial_state=True)
            for ch in range(1, NUM_CHANNELS + 1):
                assert b.is_on(ch) is True

    def test_init_preserves_relay_state_on_reinit(self, rpi_gpio_mock) -> None:
        """Re-creating the board must not reset relays already set as outputs."""
        # Simulate a previous process that left channel 1 ON (pin LOW) and
        # the remaining channels OFF (pin HIGH), all pins already OUTPUT.
        rpi_gpio_mock.gpio_function.side_effect = lambda pin: rpi_gpio_mock.OUT
        rpi_gpio_mock.input.side_effect = (
            lambda pin: rpi_gpio_mock.LOW if pin == _CHANNEL_PINS[1] else rpi_gpio_mock.HIGH
        )
        with patch("relay_tools.waveshare.GPIO", rpi_gpio_mock), \
             patch("relay_tools.waveshare._HAS_RPIGPIO", True):
            from relay_tools.waveshare import WaveshareRelayBoardRPiGPIO
            b = WaveshareRelayBoardRPiGPIO()
            # Relay state must be preserved, not reset.
            assert b.is_on(1) is True
            assert b.is_on(2) is False
        # Neither GPIO.setup() nor GPIO.output() should have been called.
        rpi_gpio_mock.setup.assert_not_called()
        rpi_gpio_mock.output.assert_not_called()

    def test_close_calls_gpio_cleanup(self, rpi_board, rpi_gpio_mock) -> None:
        rpi_board.close()
        rpi_gpio_mock.cleanup.assert_called_once()

    def test_context_manager_calls_close(self, rpi_gpio_mock) -> None:
        with patch("relay_tools.waveshare.GPIO", rpi_gpio_mock), \
             patch("relay_tools.waveshare._HAS_RPIGPIO", True):
            from relay_tools.waveshare import WaveshareRelayBoardRPiGPIO
            with WaveshareRelayBoardRPiGPIO():
                pass
        rpi_gpio_mock.cleanup.assert_called_once()

