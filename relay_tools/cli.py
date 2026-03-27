"""Command-line interface for relay-tools.

Usage examples
--------------
# Turn channel 1 ON
relay-tools on 1

# Turn channel 3 OFF
relay-tools off 3

# Toggle channel 2
relay-tools toggle 2

# Show state of all channels
relay-tools status

# Turn all channels ON
relay-tools all-on

# Turn all channels OFF
relay-tools all-off
"""

from __future__ import annotations

import click

from .waveshare import WaveshareRelayBoard


def _get_board() -> WaveshareRelayBoard:
    """Instantiate the relay board (raises on non-Pi hardware)."""
    return WaveshareRelayBoard()


@click.group()
def cli() -> None:
    """relay-tools – control the Waveshare RPi Relay Board (B)."""


@cli.command("on")
@click.argument("channel", type=int)
def cmd_on(channel: int) -> None:
    """Turn relay CHANNEL on (close the contact)."""
    with _get_board() as board:
        board.turn_on(channel)
    click.echo(f"Channel {channel}: ON")


@cli.command("off")
@click.argument("channel", type=int)
def cmd_off(channel: int) -> None:
    """Turn relay CHANNEL off (open the contact)."""
    with _get_board() as board:
        board.turn_off(channel)
    click.echo(f"Channel {channel}: OFF")


@cli.command()
@click.argument("channel", type=int)
def toggle(channel: int) -> None:
    """Toggle relay CHANNEL."""
    with _get_board() as board:
        if board.is_on(channel):
            board.turn_off(channel)
            state = "OFF"
        else:
            board.turn_on(channel)
            state = "ON"
    click.echo(f"Channel {channel}: {state}")


@cli.command()
def status() -> None:
    """Print the state of every relay channel."""
    with _get_board() as board:
        for ch, active in board.get_state().items():
            label = "ON " if active else "OFF"
            click.echo(f"  Channel {ch:>2}: {label}")


@cli.command("all-on")
def cmd_all_on() -> None:
    """Turn ALL relay channels on."""
    with _get_board() as board:
        board.turn_on_all()
    click.echo("All channels: ON")


@cli.command("all-off")
def cmd_all_off() -> None:
    """Turn ALL relay channels off."""
    with _get_board() as board:
        board.turn_off_all()
    click.echo("All channels: OFF")
