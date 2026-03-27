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
    """TestClient with WaveshareRelayBoard constructor mocked."""
    with patch("relay_tools.api.WaveshareRelayBoard", return_value=mock_board):
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
