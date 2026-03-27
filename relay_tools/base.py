"""Abstract base class for relay board drivers."""

from abc import ABC, abstractmethod


class AbstractRelayBoard(ABC):
    """Abstract interface for a relay board.

    Implementations must support reading and writing the state of
    individual relay channels identified by a 1-based channel number.
    """

    @property
    @abstractmethod
    def num_channels(self) -> int:
        """Return the total number of relay channels on the board."""

    @abstractmethod
    def turn_on(self, channel: int) -> None:
        """Activate (close) relay *channel*.

        Args:
            channel: 1-based channel number (1 … num_channels).

        Raises:
            ValueError: If *channel* is out of the valid range.
        """

    @abstractmethod
    def turn_off(self, channel: int) -> None:
        """Deactivate (open) relay *channel*.

        Args:
            channel: 1-based channel number (1 … num_channels).

        Raises:
            ValueError: If *channel* is out of the valid range.
        """

    @abstractmethod
    def is_on(self, channel: int) -> bool:
        """Return ``True`` if relay *channel* is currently active (closed).

        Args:
            channel: 1-based channel number (1 … num_channels).

        Raises:
            ValueError: If *channel* is out of the valid range.
        """

    def get_state(self) -> dict[int, bool]:
        """Return a mapping of channel → active state for all channels."""
        return {ch: self.is_on(ch) for ch in range(1, self.num_channels + 1)}

    def turn_on_all(self) -> None:
        """Activate all relay channels."""
        for ch in range(1, self.num_channels + 1):
            self.turn_on(ch)

    def turn_off_all(self) -> None:
        """Deactivate all relay channels."""
        for ch in range(1, self.num_channels + 1):
            self.turn_off(ch)

    def _validate_channel(self, channel: int) -> None:
        """Raise :class:`ValueError` if *channel* is out of range."""
        if not (1 <= channel <= self.num_channels):
            raise ValueError(
                f"Channel {channel} is out of range. "
                f"Valid channels: 1–{self.num_channels}."
            )
