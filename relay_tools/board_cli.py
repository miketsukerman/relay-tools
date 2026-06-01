"""Board-level CLI built on top of the relay daemon client API."""

from __future__ import annotations

import logging

import click

from relay_tools.board import BoardController, BoardExecutionError, WorkflowResult
from relay_tools.board_config import (
    DEFAULT_BOARD_CONFIG,
    BoardConfigError,
    load_board_profile,
)
from relay_tools.client import DEFAULT_URL, RelayClient, RelayClientError

logger = logging.getLogger(__name__)


def _client(url: str) -> RelayClient:
    return RelayClient(url)


def _load_controller(url: str, config_path: str) -> tuple[RelayClient, BoardController]:
    relay_client = _client(url)
    profile = load_board_profile(config_path)
    return relay_client, BoardController(profile, relay_client)


def _emit_result(result: WorkflowResult) -> None:
    for step in result.steps:
        click.echo(f"- {step.name}: {step.detail}")
    click.echo("Final relay state:")
    for channel in result.final_status.relay_state.channels:
        label = "ON " if channel.on else "OFF"
        click.echo(f"  Channel {channel.channel:>2}: {label}")
    if result.final_status.signals:
        click.echo("Board signals:")
        for signal_name, active in sorted(result.final_status.signals.items()):
            label = "ACTIVE" if active else "inactive"
            click.echo(f"  {signal_name}: {label}")
    if result.final_status.switches:
        click.echo("Board switches:")
        for switch_name, active in sorted(result.final_status.switches.items()):
            label = "ACTIVE" if active else "inactive"
            click.echo(f"  {switch_name}: {label}")
    if result.final_status.matching_boot_modes:
        click.echo(
            "Matching boot modes: "
            + ", ".join(result.final_status.matching_boot_modes)
        )


def _run_board_action(
    *,
    url: str,
    config_path: str,
    callback,
) -> WorkflowResult | None:
    try:
        relay_client, controller = _load_controller(url, config_path)
        with relay_client:
            return callback(controller)
    except (BoardConfigError, RelayClientError, BoardExecutionError) as exc:
        raise click.ClickException(str(exc)) from exc


@click.group()
@click.option(
    "--url",
    default=DEFAULT_URL,
    show_default=True,
    envvar="RELAY_API_URL",
    help="Base URL of the relay daemon API.",
)
@click.option(
    "--config",
    "config_path",
    default=DEFAULT_BOARD_CONFIG,
    show_default=True,
    envvar="RELAY_BOARD_CONFIG",
    help="Path to the board profile YAML file.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Enable verbose (DEBUG) logging.",
)
@click.pass_context
def board_cli(ctx: click.Context, url: str, config_path: str, verbose: bool) -> None:
    """relay-board – board-level relay workflows over HTTP."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    ctx.ensure_object(dict)
    ctx.obj["url"] = url
    ctx.obj["config_path"] = config_path


def _common_options(fn):
    fn = click.option(
        "--verify/--no-verify",
        default=None,
        help="Verify relay state after each step.",
    )(fn)
    fn = click.option(
        "--force",
        is_flag=True,
        default=False,
        help="Allow risky boot-mode transitions.",
    )(fn)
    return fn


def _resolve_verify(controller: BoardController, verify: bool | None) -> bool:
    return controller.profile.defaults.verify if verify is None else verify


@board_cli.command("status")
@click.pass_context
def cmd_status(ctx: click.Context) -> None:
    """Show current board signal/switch state and matching boot modes."""

    def _callback(controller: BoardController) -> WorkflowResult:
        board_status = controller.status()
        return WorkflowResult(steps=tuple(), final_status=board_status)

    result = _run_board_action(
        url=ctx.obj["url"],
        config_path=ctx.obj["config_path"],
        callback=_callback,
    )
    if result is not None:
        _emit_result(result)


@board_cli.command("power-on")
@_common_options
@click.pass_context
def cmd_power_on(
    ctx: click.Context,
    verify: bool | None,
    force: bool,
) -> None:
    """Execute the board power-on workflow."""

    result = _run_board_action(
        url=ctx.obj["url"],
        config_path=ctx.obj["config_path"],
        callback=lambda controller: controller.power_on(
            verify=_resolve_verify(controller, verify),
            force=force,
        ),
    )
    if result is not None:
        _emit_result(result)


@board_cli.command("power-off")
@_common_options
@click.pass_context
def cmd_power_off(
    ctx: click.Context,
    verify: bool | None,
    force: bool,
) -> None:
    """Execute the board power-off workflow."""

    result = _run_board_action(
        url=ctx.obj["url"],
        config_path=ctx.obj["config_path"],
        callback=lambda controller: controller.power_off(
            verify=_resolve_verify(controller, verify),
            force=force,
        ),
    )
    if result is not None:
        _emit_result(result)


@board_cli.command("reset")
@_common_options
@click.pass_context
def cmd_reset(ctx: click.Context, verify: bool | None, force: bool) -> None:
    """Execute the board reset workflow."""

    result = _run_board_action(
        url=ctx.obj["url"],
        config_path=ctx.obj["config_path"],
        callback=lambda controller: controller.reset(
            verify=_resolve_verify(controller, verify),
            force=force,
        ),
    )
    if result is not None:
        _emit_result(result)


@board_cli.command("power-cycle")
@_common_options
@click.pass_context
def cmd_power_cycle(
    ctx: click.Context,
    verify: bool | None,
    force: bool,
) -> None:
    """Execute the board power-cycle workflow."""

    result = _run_board_action(
        url=ctx.obj["url"],
        config_path=ctx.obj["config_path"],
        callback=lambda controller: controller.power_cycle(
            verify=_resolve_verify(controller, verify),
            force=force,
        ),
    )
    if result is not None:
        _emit_result(result)


@board_cli.command("set-boot-mode")
@click.argument("mode")
@_common_options
@click.pass_context
def cmd_set_boot_mode(
    ctx: click.Context,
    mode: str,
    verify: bool | None,
    force: bool,
) -> None:
    """Apply a named boot-mode profile."""

    result = _run_board_action(
        url=ctx.obj["url"],
        config_path=ctx.obj["config_path"],
        callback=lambda controller: controller.set_boot_mode(
            mode,
            verify=_resolve_verify(controller, verify),
            force=force,
        ),
    )
    if result is not None:
        _emit_result(result)


@board_cli.command("boot-and-wait")
@click.argument("mode")
@_common_options
@click.pass_context
def cmd_boot_and_wait(
    ctx: click.Context,
    mode: str,
    verify: bool | None,
    force: bool,
) -> None:
    """Apply a boot mode, perform the boot workflow, and exit."""

    result = _run_board_action(
        url=ctx.obj["url"],
        config_path=ctx.obj["config_path"],
        callback=lambda controller: controller.boot_and_wait(
            mode,
            verify=_resolve_verify(controller, verify),
            force=force,
        ),
    )
    if result is not None:
        _emit_result(result)


def main() -> None:  # pragma: no cover
    board_cli()
