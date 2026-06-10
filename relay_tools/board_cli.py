"""Board-level CLI built on top of the relay daemon client API."""

from __future__ import annotations

import logging

import click

from relay_tools.board import BoardController, BoardExecutionError, WorkflowResult
from relay_tools.board_config import (
    DEFAULT_BOARD_CONFIG_DIR,
    BoardConfigError,
    load_board_profile,
    resolve_board_config_name,
    resolve_default_board_config_path,
)
from relay_tools.client import DEFAULT_URL, RelayClient, RelayClientError

logger = logging.getLogger(__name__)


class BoardCLIGroup(click.Group):
    """Group that accepts an optional leading board config name."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if args and not args[0].startswith("-") and args[0] not in self.commands:
            ctx.meta["board_config_name"] = args.pop(0)
        return super().parse_args(ctx, args)


def _client(url: str) -> RelayClient:
    return RelayClient(url)


def _load_controller(url: str, config_path: str) -> tuple[RelayClient, BoardController]:
    relay_client = _client(url)
    profile = load_board_profile(config_path)
    return relay_client, BoardController(profile, relay_client)


def _resolve_config_path(config_name: str | None, config_path: str | None) -> str:
    if config_name and config_path:
        raise click.UsageError(
            "Specify either board config name or --config path, not both."
        )
    if config_path:
        return config_path
    if config_name:
        try:
            return resolve_board_config_name(
                config_name,
                config_dir=DEFAULT_BOARD_CONFIG_DIR,
            )
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint="config_name") from exc
    try:
        return resolve_default_board_config_path(config_dir=DEFAULT_BOARD_CONFIG_DIR)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc


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


@click.group(cls=BoardCLIGroup)
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
    default=None,
    help=(
        "Path to the board profile YAML file. Precedence: --config > config_name > "
        "RELAY_BOARD_CONFIG > RELAY_BOARD_DEFAULT."
    ),
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Enable verbose (DEBUG) logging.",
)
@click.pass_context
def board_cli(
    ctx: click.Context,
    url: str,
    config_path: str | None,
    verbose: bool,
) -> None:
    """relay-board – board-level relay workflows over HTTP."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    ctx.ensure_object(dict)
    config_name = ctx.meta.get("board_config_name")
    ctx.obj["url"] = url
    ctx.obj["config_path"] = _resolve_config_path(config_name, config_path)


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


@board_cli.command("run-workflow")
@click.argument("workflow_name")
@_common_options
@click.pass_context
def cmd_run_workflow(
    ctx: click.Context,
    workflow_name: str,
    verify: bool | None,
    force: bool,
) -> None:
    """Execute a named workflow from the board profile."""

    result = _run_board_action(
        url=ctx.obj["url"],
        config_path=ctx.obj["config_path"],
        callback=lambda controller: controller.run_workflow(
            workflow_name,
            verify=_resolve_verify(controller, verify),
            force=force,
        ),
    )
    if result is not None:
        _emit_result(result)


@board_cli.command("flash-internal-memory")
@_common_options
@click.pass_context
def cmd_flash_internal_memory(
    ctx: click.Context,
    verify: bool | None,
    force: bool,
) -> None:
    """Execute the flash-internal-memory workflow."""

    result = _run_board_action(
        url=ctx.obj["url"],
        config_path=ctx.obj["config_path"],
        callback=lambda controller: controller.flash_internal_memory(
            verify=_resolve_verify(controller, verify),
            force=force,
        ),
    )
    if result is not None:
        _emit_result(result)


def main() -> None:  # pragma: no cover
    board_cli()
