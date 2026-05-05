"""Tests for the FastAPI REST API."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from relay_tools.api import app

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

    board.num_channels = NUM_CHANNELS
    board.is_on.side_effect = lambda ch: state[ch]
    board.get_state.return_value = dict(state)

    def _turn_on(ch):
        state[ch] = True
        board.get_state.return_value = dict(state)

    def _turn_off(ch):
        state[ch] = False
        board.get_state.return_value = dict(state)

    def _turn_on_all():
        for ch in list(state):
            state[ch] = True
        board.get_state.return_value = dict(state)

    def _turn_off_all():
        for ch in list(state):
            state[ch] = False
        board.get_state.return_value = dict(state)

    board.turn_on.side_effect = _turn_on
    board.turn_off.side_effect = _turn_off
    board.turn_on_all.side_effect = _turn_on_all
    board.turn_off_all.side_effect = _turn_off_all
    board.close = MagicMock()
    return board


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_board():
    return _make_board()


@pytest.fixture()
def client(mock_board):
    """TestClient with _create_board mocked to return a board mock."""
    with patch("relay_tools.api._create_board", return_value=mock_board):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRelayAPI:
    def test_get_all(self, client) -> None:
        resp = client.get("/relays")
        assert resp.status_code == 200
        data = resp.json()
        assert "channels" in data
        assert len(data["channels"]) == NUM_CHANNELS

    def test_get_channel(self, client) -> None:
        resp = client.get("/relays/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["channel"] == 1
        assert data["on"] is False

    def test_get_channel_invalid(self, client, mock_board) -> None:
        mock_board.is_on.side_effect = ValueError("out of range")
        resp = client.get("/relays/99")
        assert resp.status_code == 404

    def test_channel_on(self, client) -> None:
        resp = client.post("/relays/1/on")
        assert resp.status_code == 200
        assert resp.json()["on"] is True

    def test_channel_off(self, client) -> None:
        resp = client.post("/relays/1/off")
        assert resp.status_code == 200
        assert resp.json()["on"] is False

    def test_channel_toggle_off_to_on(self, client, mock_board) -> None:
        # Channel 2 starts OFF → toggle should turn it ON
        resp = client.post("/relays/2/toggle")
        assert resp.status_code == 200
        assert resp.json()["on"] is True

    def test_channel_toggle_on_to_off(self, client, mock_board) -> None:
        # Pre-activate channel 3
        mock_board.is_on.side_effect = lambda ch: ch == 3
        resp = client.post("/relays/3/toggle")
        assert resp.status_code == 200
        assert resp.json()["on"] is False

    def test_all_on(self, client) -> None:
        resp = client.post("/relays/on")
        assert resp.status_code == 200
        data = resp.json()
        assert all(ch["on"] for ch in data["channels"])

    def test_all_off(self, client) -> None:
        resp = client.post("/relays/off")
        assert resp.status_code == 200
        data = resp.json()
        assert not any(ch["on"] for ch in data["channels"])


class TestCreateBoard:
    """Unit tests for the _create_board factory function."""

    def test_auto_uses_rpigpio_first(self) -> None:
        from relay_tools.api import _create_board
        mock_board = MagicMock()
        with patch(
            "relay_tools.api.WaveshareRelayBoardRPiGPIO", return_value=mock_board
        ) as mock_cls:
            board = _create_board("auto")
        assert board is mock_board
        mock_cls.assert_called_once_with(initial_state=False)

    def test_auto_uses_rpigpio_first_with_custom_initial_state(self) -> None:
        from relay_tools.api import _create_board
        mock_board = MagicMock()
        with patch(
            "relay_tools.api.WaveshareRelayBoardRPiGPIO", return_value=mock_board
        ) as mock_cls:
            board = _create_board("auto", initial_state=True)
        assert board is mock_board
        mock_cls.assert_called_once_with(initial_state=True)

    def test_auto_falls_back_to_gpiozero(self) -> None:
        from relay_tools.api import _create_board
        mock_board = MagicMock()
        with (
            patch(
                "relay_tools.api.WaveshareRelayBoardRPiGPIO",
                side_effect=ImportError("no RPi.GPIO"),
            ),
            patch("relay_tools.api.WaveshareRelayBoard", return_value=mock_board) as mock_cls,
        ):
            board = _create_board("auto")
        assert board is mock_board
        mock_cls.assert_called_once_with(initial_state=False)

    def test_auto_raises_when_both_missing(self) -> None:
        from relay_tools.api import _create_board
        with (
            patch(
                "relay_tools.api.WaveshareRelayBoardRPiGPIO",
                side_effect=ImportError("no RPi.GPIO"),
            ),
            patch(
                "relay_tools.api.WaveshareRelayBoard",
                side_effect=ImportError("no gpiozero"),
            ),
        ):
            with pytest.raises(RuntimeError, match="No GPIO library"):
                _create_board("auto")

    def test_rpigpio_raises_when_missing(self) -> None:
        from relay_tools.api import _create_board
        with patch(
            "relay_tools.api.WaveshareRelayBoardRPiGPIO",
            side_effect=ImportError("no RPi.GPIO"),
        ):
            with pytest.raises(RuntimeError, match="RPi.GPIO"):
                _create_board("rpigpio")

    def test_gpiozero_raises_when_missing(self) -> None:
        from relay_tools.api import _create_board
        with patch(
            "relay_tools.api.WaveshareRelayBoard",
            side_effect=ImportError("no gpiozero"),
        ):
            with pytest.raises(RuntimeError, match="gpiozero"):
                _create_board("gpiozero")

    def test_lifespan_creates_board_with_all_off(self, monkeypatch) -> None:
        """_lifespan passes initial_state=False to _create_board."""
        monkeypatch.delenv("RELAY_CONFIG", raising=False)
        mock_board = MagicMock()
        with patch(
            "relay_tools.api._create_board", return_value=mock_board
        ) as mock_factory:
            with TestClient(app, raise_server_exceptions=True):
                pass
        mock_factory.assert_called_once_with("auto", initial_state=False)

    def test_lifespan_reads_relay_driver_env(self, monkeypatch) -> None:
        """_lifespan passes RELAY_DRIVER env var to _create_board."""
        monkeypatch.setenv("RELAY_DRIVER", "rpigpio")
        monkeypatch.delenv("RELAY_CONFIG", raising=False)
        mock_board = MagicMock()
        with patch(
            "relay_tools.api._create_board", return_value=mock_board
        ) as mock_factory:
            with TestClient(app, raise_server_exceptions=True):
                pass
        mock_factory.assert_called_once_with("rpigpio", initial_state=False)

    def test_lifespan_applies_channel_config(self, monkeypatch, tmp_path) -> None:
        """_lifespan turns on channels listed as 'on' in the YAML config."""
        config = tmp_path / "channels.yaml"
        config.write_text("channels:\n  1: on\n  3: on\n")
        monkeypatch.setenv("RELAY_CONFIG", str(config))
        monkeypatch.delenv("RELAY_DRIVER", raising=False)
        mock_board = MagicMock()
        with patch("relay_tools.api._create_board", return_value=mock_board):
            with TestClient(app, raise_server_exceptions=True):
                pass
        # Channels 1 and 3 should be turned on; no turn_off calls from config.
        turn_on_calls = [call.args[0] for call in mock_board.turn_on.call_args_list]
        assert 1 in turn_on_calls
        assert 3 in turn_on_calls

    def test_lifespan_applies_off_channels_from_config(self, monkeypatch, tmp_path) -> None:
        """Channels explicitly set to off in config call turn_off."""
        config = tmp_path / "channels.yaml"
        config.write_text("channels:\n  2: off\n")
        monkeypatch.setenv("RELAY_CONFIG", str(config))
        monkeypatch.delenv("RELAY_DRIVER", raising=False)
        mock_board = MagicMock()
        with patch("relay_tools.api._create_board", return_value=mock_board):
            with TestClient(app, raise_server_exceptions=True):
                pass
        turn_off_calls = [call.args[0] for call in mock_board.turn_off.call_args_list]
        assert 2 in turn_off_calls

    def test_lifespan_no_config_no_channel_calls(self, monkeypatch) -> None:
        """Without RELAY_CONFIG no per-channel calls are made after board creation."""
        monkeypatch.delenv("RELAY_CONFIG", raising=False)
        monkeypatch.delenv("RELAY_DRIVER", raising=False)
        mock_board = MagicMock()
        with patch("relay_tools.api._create_board", return_value=mock_board):
            with TestClient(app, raise_server_exceptions=True):
                pass
        mock_board.turn_on.assert_not_called()
        mock_board.turn_off.assert_not_called()
