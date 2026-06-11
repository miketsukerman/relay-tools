"""Tests for package-level exports in relay_tools.__init__."""

from relay_tools import AbstractRelayBoard


def test_abstract_relay_board_is_exported() -> None:
    """AbstractRelayBoard is the only public export from the package root."""
    assert AbstractRelayBoard.__name__ == "AbstractRelayBoard"


def test_gpio_classes_not_in_package_namespace() -> None:
    """GPIO board classes are not exported at the package level."""
    import relay_tools

    assert not hasattr(relay_tools, "WaveshareRelayBoard")
    assert not hasattr(relay_tools, "WaveshareRelayBoardRPiGPIO")
