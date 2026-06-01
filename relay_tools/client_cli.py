"""HTTP client CLI for relay-tools daemon.

Communicates with a running ``relay serve`` (or ``relay-api``) daemon over
HTTP instead of driving GPIO directly.  Use this on any machine that can
reach the daemon — no GPIO hardware or libraries are required.

Usage examples
--------------
# Turn channel 1 ON
relay-client on 1

# Turn channel 3 OFF
relay-client off 3

# Toggle channel 2
relay-client toggle 2

# Press channel 2 (momentary ON then OFF)
relay-client press 2

# Show state of all channels
relay-client status

# Turn all channels ON
relay-client all-on

# Turn all channels OFF
relay-client all-off

# Connect to a daemon on a different host / port
relay-client --url http://pi.local:9000 status
"""

from __future__ import annotations

import logging

import click

from .client import (
    DEFAULT_URL,
    RelayClient,
    RelayClientError,
)

logger = logging.getLogger(__name__)

_DEFAULT_URL = DEFAULT_URL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client(url: str) -> RelayClient:
    """Return a reusable relay daemon client."""
    return RelayClient(url)


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
@click.option(
    "--url",
    default=_DEFAULT_URL,
    show_default=True,
    envvar="RELAY_API_URL",
    help="Base URL of the relay daemon API.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Enable verbose (DEBUG) logging.",
)
@click.pass_context
def client_cli(ctx: click.Context, url: str, verbose: bool) -> None:
    """relay-client – control the relay daemon over HTTP."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logger.debug("Verbose logging enabled.")
    ctx.ensure_object(dict)
    ctx.obj["url"] = url


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@client_cli.command("on")
@click.argument("channel", type=int)
@click.pass_context
def cmd_on(ctx: click.Context, channel: int) -> None:
    """Turn relay CHANNEL on (close the contact)."""
    url = ctx.obj["url"]
    logger.debug("relay-client on %d via %s", channel, url)
    try:
        with _client(url) as client:
            data = client.on(channel)
    except RelayClientError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Channel {data.channel}: ON")


@client_cli.command("off")
@click.argument("channel", type=int)
@click.pass_context
def cmd_off(ctx: click.Context, channel: int) -> None:
    """Turn relay CHANNEL off (open the contact)."""
    url = ctx.obj["url"]
    logger.debug("relay-client off %d via %s", channel, url)
    try:
        with _client(url) as client:
            data = client.off(channel)
    except RelayClientError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Channel {data.channel}: OFF")


@client_cli.command("toggle")
@click.argument("channel", type=int)
@click.pass_context
def cmd_toggle(ctx: click.Context, channel: int) -> None:
    """Toggle relay CHANNEL."""
    url = ctx.obj["url"]
    logger.debug("relay-client toggle %d via %s", channel, url)
    try:
        with _client(url) as client:
            data = client.toggle(channel)
    except RelayClientError as exc:
        raise click.ClickException(str(exc)) from exc
    state = "ON" if data.on else "OFF"
    click.echo(f"Channel {data.channel}: {state}")


@client_cli.command("press")
@click.argument("channel", type=int)
@click.option(
    "--duration",
    type=click.FloatRange(min=0.01),
    default=0.2,
    show_default=True,
    help="Seconds to keep the relay on before switching it off.",
)
@click.pass_context
def cmd_press(ctx: click.Context, channel: int, duration: float) -> None:
    """Momentarily press relay CHANNEL (on, hold, then off)."""
    url = ctx.obj["url"]
    logger.debug("relay-client press %d via %s (duration=%.3f)", channel, url, duration)
    try:
        with _client(url) as client:
            data = client.press(channel, duration)
    except RelayClientError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Channel {data.channel}: PRESSED")


@client_cli.command("status")
@click.pass_context
def cmd_status(ctx: click.Context) -> None:
    """Print the state of every relay channel."""
    url = ctx.obj["url"]
    logger.debug("relay-client status via %s", url)
    try:
        with _client(url) as client:
            data = client.status()
    except RelayClientError as exc:
        raise click.ClickException(str(exc)) from exc
    for ch in data.channels:
        label = "ON " if ch.on else "OFF"
        click.echo(f"  Channel {ch.channel:>2}: {label}")


@client_cli.command("all-on")
@click.pass_context
def cmd_all_on(ctx: click.Context) -> None:
    """Turn ALL relay channels on."""
    url = ctx.obj["url"]
    logger.debug("relay-client all-on via %s", url)
    try:
        with _client(url) as client:
            client.all_on()
    except RelayClientError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("All channels: ON")


@client_cli.command("all-off")
@click.pass_context
def cmd_all_off(ctx: click.Context) -> None:
    """Turn ALL relay channels off."""
    url = ctx.obj["url"]
    logger.debug("relay-client all-off via %s", url)
    try:
        with _client(url) as client:
            client.all_off()
    except RelayClientError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("All channels: OFF")


# ---------------------------------------------------------------------------
# Standalone entry point (relay-client script)
# ---------------------------------------------------------------------------


def main() -> None:  # pragma: no cover
    """Entry point for the ``relay-client`` console script."""
    client_cli()
