"""Waveshare RPi Relay Board (B) driver.

Hardware reference
------------------
https://www.waveshare.com/wiki/RPi_Relay_Board_(B)

The board exposes 8 relay channels wired to the following Raspberry Pi BCM
GPIO pins (active-LOW logic – the relay closes when the output is driven LOW):

    Channel 1 → BCM 5
    Channel 2 → BCM 6
    Channel 3 → BCM 13
    Channel 4 → BCM 16
    Channel 5 → BCM 19
    Channel 6 → BCM 20
    Channel 7 → BCM 21
    Channel 8 → BCM 26

``gpiozero`` is used so that the driver works on both Raspberry Pi 4 and
Raspberry Pi 5 (which requires the ``lgpio`` pin factory instead of the
legacy ``RPi.GPIO`` backend).
"""

from __future__ import annotations

import logging

from .base import AbstractRelayBoard

logger = logging.getLogger(__name__)

try:
    from gpiozero import OutputDevice
    _HAS_GPIOZERO = True
except ImportError:  # pragma: no cover
    OutputDevice = None  # type: ignore[assignment,misc]
    _HAS_GPIOZERO = False

# BCM pin numbers indexed by 1-based channel (index 0 is unused).
_CHANNEL_PINS: tuple[int, ...] = (
    0,   # placeholder so that index 1 maps to the first real pin
    5,   # CH1
    6,   # CH2
    13,  # CH3
    16,  # CH4
    19,  # CH5
    20,  # CH6
    21,  # CH7
    26,  # CH8
)

NUM_CHANNELS = 8


class WaveshareRelayBoard(AbstractRelayBoard):
    """Driver for the Waveshare RPi Relay Board (B).

    The board is controlled via GPIO using :mod:`gpiozero`.  On
    Raspberry Pi 5 you must have the ``lgpio`` package installed so that
    gpiozero selects the compatible pin factory automatically.

    Args:
        initial_state: If ``True`` all relays are activated on
            initialisation; if ``False`` (default) all relays are
            deactivated.
    """

    def __init__(self, *, initial_state: bool = False) -> None:
        if not _HAS_GPIOZERO or OutputDevice is None:  # pragma: no cover
            raise ImportError(
                "gpiozero is required for WaveshareRelayBoard. "
                "Install it with: pip install relay-tools[gpio]"
            )

        # active_high=False → on() drives the pin LOW (activates relay)
        self._relays = [
            OutputDevice(
                pin,
                active_high=False,
                initial_value=initial_state,
            )
            for pin in _CHANNEL_PINS[1:]  # skip index-0 placeholder
        ]

    # ------------------------------------------------------------------
    # AbstractRelayBoard interface
    # ------------------------------------------------------------------

    @property
    def num_channels(self) -> int:
        return NUM_CHANNELS

    def turn_on(self, channel: int) -> None:
        """Activate relay *channel* (close the relay contact)."""
        self._validate_channel(channel)
        logger.debug("GPIO: channel %d → ON (pin %d)", channel, _CHANNEL_PINS[channel])
        self._relays[channel - 1].on()

    def turn_off(self, channel: int) -> None:
        """Deactivate relay *channel* (open the relay contact)."""
        self._validate_channel(channel)
        logger.debug("GPIO: channel %d → OFF (pin %d)", channel, _CHANNEL_PINS[channel])
        self._relays[channel - 1].off()

    def is_on(self, channel: int) -> bool:
        """Return ``True`` if relay *channel* is currently active."""
        self._validate_channel(channel)
        state = bool(self._relays[channel - 1].value)
        logger.debug("GPIO: channel %d state = %s", channel, "ON" if state else "OFF")
        return state

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Release all GPIO resources."""
        logger.debug("Closing GPIO resources for all %d channels", len(self._relays))
        for relay in self._relays:
            relay.close()

    def __enter__(self) -> "WaveshareRelayBoard":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
