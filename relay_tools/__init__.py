"""relay_tools – Waveshare RPi Relay Hat tooling."""

from .base import AbstractRelayBoard
from .waveshare import WaveshareRelayBoard

__all__ = ["AbstractRelayBoard", "WaveshareRelayBoard"]
