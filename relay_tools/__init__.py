"""relay_tools – Waveshare RPi Relay Hat tooling."""

from __future__ import annotations

from .base import AbstractRelayBoard

__all__ = [
    "AbstractRelayBoard",
    "WaveshareRelayBoard",
    "WaveshareRelayBoardRPiGPIO",
]


def __getattr__(name: str) -> object:
    """Lazily expose optional GPIO-backed board implementations."""
    if name in {"WaveshareRelayBoard", "WaveshareRelayBoardRPiGPIO"}:
        from .waveshare import WaveshareRelayBoard, WaveshareRelayBoardRPiGPIO

        return {
            "WaveshareRelayBoard": WaveshareRelayBoard,
            "WaveshareRelayBoardRPiGPIO": WaveshareRelayBoardRPiGPIO,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
