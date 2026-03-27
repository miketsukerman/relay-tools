"""Tests for the abstract relay board base class."""

import pytest

from relay_tools.base import AbstractRelayBoard


class ConcreteRelayBoard(AbstractRelayBoard):
    """Minimal concrete implementation for testing the base class logic."""

    def __init__(self, num_channels: int = 4) -> None:
        self._num_channels = num_channels
        self._state: dict[int, bool] = {
            ch: False for ch in range(1, num_channels + 1)
        }

    @property
    def num_channels(self) -> int:
        return self._num_channels

    def turn_on(self, channel: int) -> None:
        self._validate_channel(channel)
        self._state[channel] = True

    def turn_off(self, channel: int) -> None:
        self._validate_channel(channel)
        self._state[channel] = False

    def is_on(self, channel: int) -> bool:
        self._validate_channel(channel)
        return self._state[channel]


class TestAbstractRelayBoard:
    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            AbstractRelayBoard()  # type: ignore[abstract]

    def test_num_channels(self) -> None:
        board = ConcreteRelayBoard(num_channels=8)
        assert board.num_channels == 8

    def test_turn_on_and_is_on(self) -> None:
        board = ConcreteRelayBoard()
        board.turn_on(1)
        assert board.is_on(1) is True

    def test_turn_off(self) -> None:
        board = ConcreteRelayBoard()
        board.turn_on(2)
        board.turn_off(2)
        assert board.is_on(2) is False

    def test_get_state_all_off(self) -> None:
        board = ConcreteRelayBoard(num_channels=4)
        state = board.get_state()
        assert state == {1: False, 2: False, 3: False, 4: False}

    def test_get_state_after_on(self) -> None:
        board = ConcreteRelayBoard(num_channels=4)
        board.turn_on(3)
        assert board.get_state()[3] is True

    def test_turn_on_all(self) -> None:
        board = ConcreteRelayBoard(num_channels=4)
        board.turn_on_all()
        assert all(board.get_state().values())

    def test_turn_off_all(self) -> None:
        board = ConcreteRelayBoard(num_channels=4)
        board.turn_on_all()
        board.turn_off_all()
        assert not any(board.get_state().values())

    def test_validate_channel_too_low(self) -> None:
        board = ConcreteRelayBoard()
        with pytest.raises(ValueError, match="out of range"):
            board.turn_on(0)

    def test_validate_channel_too_high(self) -> None:
        board = ConcreteRelayBoard(num_channels=4)
        with pytest.raises(ValueError, match="out of range"):
            board.turn_on(5)

    def test_validate_channel_boundary_valid(self) -> None:
        board = ConcreteRelayBoard(num_channels=4)
        board.turn_on(4)
        assert board.is_on(4) is True
