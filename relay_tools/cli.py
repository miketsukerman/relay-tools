"""Command-line interface for relay-tools.

Communicates with a running relay daemon over HTTP.  Use ``relay serve``
(or ``relay-api``) to start the daemon first, then run channel commands
from any host that can reach it.

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

# Connect to a daemon on a different host / port
relay --url http://pi.local:9000 on 1

# Start the relay HTTP API daemon (GPIO access only happens here)
relay serve [--host HOST] [--port PORT] [--driver DRIVER]
"""

from __future__ import annotations

import logging
import os

import click
import uvicorn

from .client import DEFAULT_URL, RelayClient, RelayClientError

logger = logging.getLogger(__name__)

_DEFAULT_URL = DEFAULT_URL


def _client(url: str) -> RelayClient:
    """Return a reusable relay daemon client."""
    return RelayClient(url)


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
def cli(ctx: click.Context, url: str, verbose: bool) -> None:
    """relay – control the Waveshare RPi Relay Board (B) via HTTP."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logger.debug("Verbose logging enabled.")
    ctx.ensure_object(dict)
    ctx.obj["url"] = url


@cli.command("on")
@click.argument("channel", type=int)
@click.pass_context
def cmd_on(ctx: click.Context, channel: int) -> None:
    """Turn relay CHANNEL on (close the contact)."""
    url = ctx.obj["url"]
    logger.debug("relay on %d via %s", channel, url)
    try:
        with _client(url) as client:
            data = client.on(channel)
    except RelayClientError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Channel {data.channel}: ON")


@cli.command("off")
@click.argument("channel", type=int)
@click.pass_context
def cmd_off(ctx: click.Context, channel: int) -> None:
    """Turn relay CHANNEL off (open the contact)."""
    url = ctx.obj["url"]
    logger.debug("relay off %d via %s", channel, url)
    try:
        with _client(url) as client:
            data = client.off(channel)
    except RelayClientError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Channel {data.channel}: OFF")


@cli.command("toggle")
@click.argument("channel", type=int)
@click.pass_context
def cmd_toggle(ctx: click.Context, channel: int) -> None:
    """Toggle relay CHANNEL."""
    url = ctx.obj["url"]
    logger.debug("relay toggle %d via %s", channel, url)
    try:
        with _client(url) as client:
            data = client.toggle(channel)
    except RelayClientError as exc:
        raise click.ClickException(str(exc)) from exc
    state = "ON" if data.on else "OFF"
    click.echo(f"Channel {data.channel}: {state}")


@cli.command("press")
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
    logger.debug("relay press %d via %s (duration=%.3f)", channel, url, duration)
    try:
        with _client(url) as client:
            data = client.press(channel, duration)
    except RelayClientError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Channel {data.channel}: PRESSED")


@cli.command("status")
@click.pass_context
def cmd_status(ctx: click.Context) -> None:
    """Print the state of every relay channel."""
    url = ctx.obj["url"]
    logger.debug("relay status via %s", url)
    try:
        with _client(url) as client:
            data = client.status()
    except RelayClientError as exc:
        raise click.ClickException(str(exc)) from exc
    for ch in data.channels:
        label = "ON " if ch.on else "OFF"
        click.echo(f"  Channel {ch.channel:>2}: {label}")


@cli.command("all-on")
@click.pass_context
def cmd_all_on(ctx: click.Context) -> None:
    """Turn ALL relay channels on."""
    url = ctx.obj["url"]
    logger.debug("relay all-on via %s", url)
    try:
        with _client(url) as client:
            client.all_on()
    except RelayClientError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("All channels: ON")


@cli.command("all-off")
@click.pass_context
def cmd_all_off(ctx: click.Context) -> None:
    """Turn ALL relay channels off."""
    url = ctx.obj["url"]
    logger.debug("relay all-off via %s", url)
    try:
        with _client(url) as client:
            client.all_off()
    except RelayClientError as exc:
        raise click.ClickException(str(exc)) from exc
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
@click.option(
    "--driver",
    type=click.Choice(["auto", "rpigpio", "gpiozero"]),
    default="auto",
    show_default=True,
    help="GPIO backend driver.  'auto' tries rpigpio first, then gpiozero.",
)
def cmd_serve(host: str, port: int, config: str | None, driver: str) -> None:
    """Start the relay HTTP API daemon.

    The selected GPIO driver is forwarded to the API process via the
    ``RELAY_DRIVER`` environment variable so the lifespan startup picks
    the correct backend.  GPIO libraries are only imported inside the
    daemon process, not by any other CLI command.

    All channels are initialised to OFF on startup.  Use --config (or set
    ``RELAY_CONFIG``) to specify a YAML file that overrides individual
    channel states.
    """
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
