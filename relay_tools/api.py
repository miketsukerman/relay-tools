"""FastAPI REST API for relay-tools.

Run with uvicorn
----------------
uvicorn relay_tools.api:app --host 0.0.0.0 --port 8000

Or via the CLI daemon subcommand:
relay serve [--host HOST] [--port PORT] [--driver DRIVER]

Or via the standalone entry-point:
relay-api

Environment variables
---------------------
RELAY_DRIVER   GPIO backend to use: "auto" (default), "rpigpio", or "gpiozero".
               "auto" tries rpigpio first and falls back to gpiozero.
RELAY_CONFIG   Path to a YAML file that defines per-channel initial states.
               If unset, or if the file does not exist, all channels start off.
               See ``relay_tools.config.load_channel_config`` for the format.

Endpoints
---------
GET  /relays                – state of all channels
GET  /relays/{channel}      – state of a single channel
POST /relays/{channel}/on   – turn channel on
POST /relays/{channel}/off  – turn channel off
POST /relays/{channel}/toggle – toggle channel
POST /relays/on             – turn all channels on
POST /relays/off            – turn all channels off
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .base import AbstractRelayBoard
from .config import load_channel_config
from .waveshare import WaveshareRelayBoard, WaveshareRelayBoardRPiGPIO

# ---------------------------------------------------------------------------
# Board factory
# ---------------------------------------------------------------------------


def _create_board(
    driver: str = "auto", initial_state: bool = False
) -> AbstractRelayBoard:
    """Instantiate the appropriate relay board backend.

    Mirrors the auto-detection logic in :func:`relay_tools.cli._get_board`
    but raises :class:`RuntimeError` (instead of a Click exception) so that
    the error surfaces cleanly during FastAPI lifespan startup.

    Args:
        driver: ``"auto"`` tries rpigpio first and falls back to gpiozero.
            ``"rpigpio"`` and ``"gpiozero"`` select the backend explicitly.
        initial_state: Initial state applied to every relay channel.
            ``False`` (default) ensures all channels start off.

    Raises:
        RuntimeError: When the requested GPIO library is not installed.
    """
    if driver == "auto":
        try:
            return WaveshareRelayBoardRPiGPIO(initial_state=initial_state)
        except ImportError:
            pass
        try:
            return WaveshareRelayBoard(initial_state=initial_state)
        except ImportError:
            raise RuntimeError(
                "No GPIO library found. "
                "Install one with: pip install relay-tools[gpio]"
            )

    if driver == "rpigpio":
        try:
            return WaveshareRelayBoardRPiGPIO(initial_state=initial_state)
        except ImportError:
            raise RuntimeError(
                "RPi.GPIO is not available. "
                "Install it with: pip install relay-tools[gpio]"
            )

    # driver == "gpiozero"
    try:
        return WaveshareRelayBoard(initial_state=initial_state)
    except ImportError:
        raise RuntimeError(
            "gpiozero is not available. "
            "Install it with: pip install relay-tools[gpio]"
        )


# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------

_board: AbstractRelayBoard | None = None


def _get_board() -> AbstractRelayBoard:
    if _board is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="Relay board not available.")
    return _board


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _board
    driver = os.environ.get("RELAY_DRIVER", "auto")
    # Always start with all channels off; per-channel overrides are applied below.
    _board = _create_board(driver, initial_state=False)

    # Apply per-channel initial states from the YAML config (if configured).
    config_path = os.environ.get("RELAY_CONFIG")
    if config_path:
        channel_states = load_channel_config(config_path)
        for channel, state in channel_states.items():
            if state:
                _board.turn_on(channel)
            else:
                _board.turn_off(channel)

    try:
        yield
    finally:
        _board.close()
        _board = None


app = FastAPI(
    title="relay-tools API",
    description="REST API to control the Waveshare RPi Relay Board (B).",
    version="0.1.0",
    lifespan=_lifespan,
)

# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ChannelState(BaseModel):
    channel: int
    on: bool


class BoardState(BaseModel):
    channels: list[ChannelState]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/relays", response_model=BoardState, summary="Get state of all channels")
def get_all() -> BoardState:
    """Return the current state of every relay channel."""
    board = _get_board()
    return BoardState(
        channels=[
            ChannelState(channel=ch, on=active)
            for ch, active in board.get_state().items()
        ]
    )


@app.get(
    "/relays/{channel}",
    response_model=ChannelState,
    summary="Get state of a single channel",
)
def get_channel(channel: int) -> ChannelState:
    """Return the current state of relay *channel*."""
    board = _get_board()
    try:
        return ChannelState(channel=channel, on=board.is_on(channel))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/relays/{channel}/on",
    response_model=ChannelState,
    summary="Turn a channel on",
)
def channel_on(channel: int) -> ChannelState:
    """Activate relay *channel* (close the contact)."""
    board = _get_board()
    try:
        board.turn_on(channel)
        return ChannelState(channel=channel, on=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/relays/{channel}/off",
    response_model=ChannelState,
    summary="Turn a channel off",
)
def channel_off(channel: int) -> ChannelState:
    """Deactivate relay *channel* (open the contact)."""
    board = _get_board()
    try:
        board.turn_off(channel)
        return ChannelState(channel=channel, on=False)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/relays/{channel}/toggle",
    response_model=ChannelState,
    summary="Toggle a channel",
)
def channel_toggle(channel: int) -> ChannelState:
    """Toggle relay *channel*."""
    board = _get_board()
    try:
        if board.is_on(channel):
            board.turn_off(channel)
            active = False
        else:
            board.turn_on(channel)
            active = True
        return ChannelState(channel=channel, on=active)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/relays/on", response_model=BoardState, summary="Turn all channels on")
def all_on() -> BoardState:
    """Activate every relay channel."""
    board = _get_board()
    board.turn_on_all()
    return BoardState(
        channels=[
            ChannelState(channel=ch, on=active)
            for ch, active in board.get_state().items()
        ]
    )


@app.post("/relays/off", response_model=BoardState, summary="Turn all channels off")
def all_off() -> BoardState:
    """Deactivate every relay channel."""
    board = _get_board()
    board.turn_off_all()
    return BoardState(
        channels=[
            ChannelState(channel=ch, on=active)
            for ch, active in board.get_state().items()
        ]
    )
