"""Command-line interface for relay-tools.

Usage examples
--------------
# Turn channel 1 ON
relay on 1

# Turn channel 3 OFF
relay off 3

# Toggle channel 2
relay toggle 2

# Press channel 2 (momentary ON then OFF)
relay press 2

# Show state of all channels
relay status

# Turn all channels ON
relay all-on

# Turn all channels OFF
relay all-off

# Use the gpiozero backend instead of RPi.GPIO
relay --driver gpiozero on 1
"""

from __future__ import annotations

import logging
import os

import click
import uvicorn

from .base import AbstractRelayBoard
from .waveshare import WaveshareRelayBoard, WaveshareRelayBoardRPiGPIO

logger = logging.getLogger(__name__)

# Sentinel used by _get_board to signal auto-detection.
_AUTO = "auto"


def _get_board(driver: str = _AUTO) -> AbstractRelayBoard:
    """Instantiate the relay board for the chosen *driver*.

    The board is **not** closed after each command so that the GPIO pin
    state (and therefore the relay position) is preserved after the
    process exits.  GPIO cleanup is left to the OS on process termination.

    When *driver* is ``"auto"`` (the default), ``rpigpio`` is tried first
    and ``gpiozero`` is used as a fallback so the CLI works regardless of
    which GPIO library is installed.  When an explicit driver is requested
    and that library is missing a :class:`click.ClickException` is raised
    with an actionable install hint.
    """
    if driver == _AUTO:
        # Try RPi.GPIO first (pin state persists after process exit).
        try:
            return WaveshareRelayBoardRPiGPIO()
        except ImportError:
            pass
        # Fall back to gpiozero.
        try:
            return WaveshareRelayBoard()
        except ImportError:
            raise click.ClickException(
                "No GPIO library found. Install one with:\n"
                "  pip install relay-tools[gpio]"
            )

    if driver == "rpigpio":
        try:
            return WaveshareRelayBoardRPiGPIO()
        except ImportError:
            raise click.ClickException(
                "RPi.GPIO is not available. Install it with:\n"
                "  pip install relay-tools[gpio]"
            )

    # driver == "gpiozero"
    try:
        return WaveshareRelayBoard()
    except ImportError:
        raise click.ClickException(
            "gpiozero is not available. Install it with:\n"
            "  pip install relay-tools[gpio]"
        )


@click.group()
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Enable verbose (DEBUG) logging.",
)
@click.option(
    "--driver",
    type=click.Choice(["auto", "rpigpio", "gpiozero"]),
    default="auto",
    show_default=True,
    help="GPIO backend driver to use.  'auto' tries rpigpio first, then gpiozero.",
)
@click.pass_context
def cli(ctx: click.Context, verbose: bool, driver: str) -> None:
    """relay – control the Waveshare RPi Relay Board (B)."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logger.debug("Verbose logging enabled.")
    ctx.ensure_object(dict)
    ctx.obj["driver"] = driver


@cli.command("on")
@click.argument("channel", type=int)
@click.pass_context
def cmd_on(ctx: click.Context, channel: int) -> None:
    """Turn relay CHANNEL on (close the contact)."""
    logger.debug("Turning channel %d ON", channel)
    board = _get_board(ctx.obj["driver"])
    board.turn_on(channel)
    click.echo(f"Channel {channel}: ON")


@cli.command("off")
@click.argument("channel", type=int)
@click.pass_context
def cmd_off(ctx: click.Context, channel: int) -> None:
    """Turn relay CHANNEL off (open the contact)."""
    logger.debug("Turning channel %d OFF", channel)
    board = _get_board(ctx.obj["driver"])
    board.turn_off(channel)
    click.echo(f"Channel {channel}: OFF")


@cli.command()
@click.argument("channel", type=int)
@click.pass_context
def toggle(ctx: click.Context, channel: int) -> None:
    """Toggle relay CHANNEL."""
    logger.debug("Toggling channel %d", channel)
    board = _get_board(ctx.obj["driver"])
    if board.is_on(channel):
        board.turn_off(channel)
        state = "OFF"
    else:
        board.turn_on(channel)
        state = "ON"
    click.echo(f"Channel {channel}: {state}")


@cli.command()
@click.argument("channel", type=int)
@click.pass_context
def press(ctx: click.Context, channel: int) -> None:
    """Momentarily press relay CHANNEL (on, then off)."""
    logger.debug("Pressing channel %d", channel)
    board = _get_board(ctx.obj["driver"])
    board.turn_on(channel)
    board.turn_off(channel)
    click.echo(f"Channel {channel}: PRESSED")


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Print the state of every relay channel."""
    logger.debug("Fetching status of all channels")
    board = _get_board(ctx.obj["driver"])
    for ch, active in board.get_state().items():
        label = "ON " if active else "OFF"
        click.echo(f"  Channel {ch:>2}: {label}")


@cli.command("all-on")
@click.pass_context
def cmd_all_on(ctx: click.Context) -> None:
    """Turn ALL relay channels on."""
    logger.debug("Turning all channels ON")
    board = _get_board(ctx.obj["driver"])
    board.turn_on_all()
    click.echo("All channels: ON")


@cli.command("all-off")
@click.pass_context
def cmd_all_off(ctx: click.Context) -> None:
    """Turn ALL relay channels off."""
    logger.debug("Turning all channels OFF")
    board = _get_board(ctx.obj["driver"])
    board.turn_off_all()
    click.echo("All channels: OFF")


@cli.command("serve")
@click.option(
    "--host",
    default="0.0.0.0",
    show_default=True,
    help="Network interface to bind the API server to.",
)
@click.option(
    "--port",
    default=8000,
    show_default=True,
    type=int,
    help="TCP port for the API server.",
)
@click.option(
    "--config",
    default=None,
    metavar="PATH",
    help=(
        "Path to a YAML file defining per-channel initial states. "
        "Defaults to the RELAY_CONFIG environment variable if set; "
        "otherwise all channels start off."
    ),
)
@click.pass_context
def cmd_serve(ctx: click.Context, host: str, port: int, config: str | None) -> None:
    """Start the relay HTTP API daemon.

    The selected GPIO driver is forwarded to the API process via the
    ``RELAY_DRIVER`` environment variable so the lifespan startup picks
    the correct backend without needing a second CLI flag.

    All channels are initialised to OFF on startup.  Use --config (or set
    ``RELAY_CONFIG``) to specify a YAML file that overrides individual
    channel states.
    """
    driver = ctx.obj["driver"]
    os.environ["RELAY_DRIVER"] = driver
    if config is not None:
        os.environ["RELAY_CONFIG"] = config
    logger.debug(
        "Starting API server on %s:%d (driver=%s, config=%s)",
        host,
        port,
        driver,
        config,
    )
    uvicorn.run("relay_tools.api:app", host=host, port=port)


def serve_api() -> None:
    """Standalone entry point for the ``relay-api`` command.

    Starts the uvicorn server on ``0.0.0.0:8000``.  The API layer reads
    the ``RELAY_DRIVER`` environment variable (default: ``"auto"``) during
    startup to select the GPIO backend.  Set it before running this command
    to choose a specific driver, e.g.::

        RELAY_DRIVER=rpigpio relay-api

    Use ``relay serve`` for full control over host, port, and driver from
    the same CLI group.
    """
    uvicorn.run("relay_tools.api:app", host="0.0.0.0", port=8000)
