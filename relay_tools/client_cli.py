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
import httpx

logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://localhost:8000"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client(url: str) -> httpx.Client:
    """Return an :class:`httpx.Client` pointed at *url*."""
    return httpx.Client(base_url=url)


def _handle_response(resp: httpx.Response) -> dict:
    """Raise a :class:`click.ClickException` on HTTP errors; return JSON body."""
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text
        try:
            detail = exc.response.json().get("detail", detail)
        except Exception:
            pass
        raise click.ClickException(
            f"Daemon returned HTTP {exc.response.status_code}: {detail}"
        ) from exc
    return resp.json()


def _connection_error(url: str) -> click.ClickException:
    return click.ClickException(
        f"Could not connect to relay daemon at {url}.\n"
        "Make sure the daemon is running: relay serve"
    )


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
    logger.debug("POST %s/relays/%d/on", url, channel)
    try:
        with _client(url) as client:
            data = _handle_response(client.post(f"/relays/{channel}/on"))
    except httpx.ConnectError:
        raise _connection_error(url)
    click.echo(f"Channel {data['channel']}: ON")


@client_cli.command("off")
@click.argument("channel", type=int)
@click.pass_context
def cmd_off(ctx: click.Context, channel: int) -> None:
    """Turn relay CHANNEL off (open the contact)."""
    url = ctx.obj["url"]
    logger.debug("POST %s/relays/%d/off", url, channel)
    try:
        with _client(url) as client:
            data = _handle_response(client.post(f"/relays/{channel}/off"))
    except httpx.ConnectError:
        raise _connection_error(url)
    click.echo(f"Channel {data['channel']}: OFF")


@client_cli.command("toggle")
@click.argument("channel", type=int)
@click.pass_context
def cmd_toggle(ctx: click.Context, channel: int) -> None:
    """Toggle relay CHANNEL."""
    url = ctx.obj["url"]
    logger.debug("POST %s/relays/%d/toggle", url, channel)
    try:
        with _client(url) as client:
            data = _handle_response(client.post(f"/relays/{channel}/toggle"))
    except httpx.ConnectError:
        raise _connection_error(url)
    state = "ON" if data["on"] else "OFF"
    click.echo(f"Channel {data['channel']}: {state}")


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
    logger.debug(
        "POST %s/relays/%d/press (duration=%.3f)", url, channel, duration
    )
    try:
        with _client(url) as client:
            data = _handle_response(
                client.post(f"/relays/{channel}/press", params={"duration": duration})
            )
    except httpx.ConnectError:
        raise _connection_error(url)
    click.echo(f"Channel {data['channel']}: PRESSED")


@client_cli.command("status")
@click.pass_context
def cmd_status(ctx: click.Context) -> None:
    """Print the state of every relay channel."""
    url = ctx.obj["url"]
    logger.debug("GET %s/relays", url)
    try:
        with _client(url) as client:
            data = _handle_response(client.get("/relays"))
    except httpx.ConnectError:
        raise _connection_error(url)
    for ch in data["channels"]:
        label = "ON " if ch["on"] else "OFF"
        click.echo(f"  Channel {ch['channel']:>2}: {label}")


@client_cli.command("all-on")
@click.pass_context
def cmd_all_on(ctx: click.Context) -> None:
    """Turn ALL relay channels on."""
    url = ctx.obj["url"]
    logger.debug("POST %s/relays/on", url)
    try:
        with _client(url) as client:
            _handle_response(client.post("/relays/on"))
    except httpx.ConnectError:
        raise _connection_error(url)
    click.echo("All channels: ON")


@client_cli.command("all-off")
@click.pass_context
def cmd_all_off(ctx: click.Context) -> None:
    """Turn ALL relay channels off."""
    url = ctx.obj["url"]
    logger.debug("POST %s/relays/off", url)
    try:
        with _client(url) as client:
            _handle_response(client.post("/relays/off"))
    except httpx.ConnectError:
        raise _connection_error(url)
    click.echo("All channels: OFF")


# ---------------------------------------------------------------------------
# Standalone entry point (relay-client script)
# ---------------------------------------------------------------------------


def main() -> None:  # pragma: no cover
    """Entry point for the ``relay-client`` console script."""
    client_cli()
