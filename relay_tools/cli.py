"""Command-line interface for relay-tools.

Usage examples
--------------
# Turn channel 1 ON
relay on 1

# Turn channel 3 OFF
relay off 3

# Toggle channel 2
relay toggle 2

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

import click

from .waveshare import WaveshareRelayBoard, WaveshareRelayBoardRPiGPIO
from .base import AbstractRelayBoard

logger = logging.getLogger(__name__)


def _get_board(driver: str = "rpigpio") -> AbstractRelayBoard:
    """Instantiate the relay board for the chosen *driver*.

    The board is **not** closed after each command so that the GPIO pin
    state (and therefore the relay position) is preserved after the
    process exits.  GPIO cleanup is left to the OS on process termination.
    """
    if driver == "rpigpio":
        return WaveshareRelayBoardRPiGPIO()
    return WaveshareRelayBoard()


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
    type=click.Choice(["rpigpio", "gpiozero"]),
    default="rpigpio",
    show_default=True,
    help="GPIO backend driver to use.",
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

