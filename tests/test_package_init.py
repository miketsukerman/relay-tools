"""Tests for package-level exports in relay_tools.__init__."""

from relay_tools import (
    AbstractRelayBoard,
    WaveshareRelayBoard,
    WaveshareRelayBoardRPiGPIO,
)


def test_package_exports_are_available() -> None:
    """Package exports should resolve lazily without changing import API."""
    assert AbstractRelayBoard.__name__ == "AbstractRelayBoard"
    assert WaveshareRelayBoard.__name__ == "WaveshareRelayBoard"
    assert WaveshareRelayBoardRPiGPIO.__name__ == "WaveshareRelayBoardRPiGPIO"
