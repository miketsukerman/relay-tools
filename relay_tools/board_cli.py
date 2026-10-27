"""Board-level CLI built on top of the relay daemon client API."""

from __future__ import annotations

import json as json_module
import logging
import socket
import sys
from pathlib import Path

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
from relay_tools.console.client import (
    CONSOLE_TOKEN_ENV,
    CONSOLE_URL_ENV,
    DEFAULT_CONSOLE_URL,
    ConsoleClient,
    ConsoleClientError,
)

logger = logging.getLogger(__name__)


class BoardCLIGroup(click.Group):
    """Group that accepts an optional leading board config name."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if args and not args[0].startswith("-") and args[0] not in self.commands:
            ctx.meta["board_config_name"] = args.pop(0)
        return super().parse_args(ctx, args)


def _client(url: str) -> RelayClient:
    return RelayClient(url)


def _console_client(ctx_obj: dict) -> ConsoleClient:
    return ConsoleClient(ctx_obj["console_url"], token=ctx_obj["console_token"])


def _load_controller(
    url: str,
    config_path: str,
    *,
    console_url: str = DEFAULT_CONSOLE_URL,
    console_token: str | None = None,
) -> tuple[RelayClient, BoardController]:
    relay_client = _client(url)
    profile = load_board_profile(config_path)
    console_client = None
    if profile.console is not None:
        console_client = ConsoleClient(console_url, token=console_token)
    return relay_client, BoardController(
        profile,
        relay_client,
        console_client=console_client,
        actor="relay-board",
        board_name=Path(config_path).stem,
    )


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
    console_url: str = DEFAULT_CONSOLE_URL,
    console_token: str | None = None,
) -> WorkflowResult | None:
    try:
        relay_client, controller = _load_controller(
            url,
            config_path,
            console_url=console_url,
            console_token=console_token,
        )
        with relay_client:
            try:
                return callback(controller)
            finally:
                if controller.console_client is not None:
                    controller.console_client.close()
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
    "--console-url",
    default=DEFAULT_CONSOLE_URL,
    show_default=True,
    envvar=CONSOLE_URL_ENV,
    help="Base URL of the relay-console service.",
)
@click.option(
    "--console-token",
    default=None,
    envvar=CONSOLE_TOKEN_ENV,
    help="Bearer token for the relay-console service.",
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
    console_url: str,
    console_token: str | None,
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
    ctx.obj["console_url"] = console_url
    ctx.obj["console_token"] = console_token
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

    power: list[str] = []

    def _callback(controller: BoardController) -> WorkflowResult:
        board_status = controller.status()
        power_switch = controller.profile.defaults.power_switch
        state = (
            board_status.switches.get(power_switch)
            if power_switch is not None
            else None
        )
        power.append("unknown" if state is None else ("on" if state else "off"))
        return WorkflowResult(steps=tuple(), final_status=board_status)

    result = _run_board_action(
        url=ctx.obj["url"],
        config_path=ctx.obj["config_path"],
        console_url=ctx.obj["console_url"],
        console_token=ctx.obj["console_token"],
        callback=_callback,
    )
    if result is not None:
        _emit_result(result)
        if power:
            click.echo(f"Power: {power[0]}")


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
        console_url=ctx.obj["console_url"],
        console_token=ctx.obj["console_token"],
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
        console_url=ctx.obj["console_url"],
        console_token=ctx.obj["console_token"],
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
        console_url=ctx.obj["console_url"],
        console_token=ctx.obj["console_token"],
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
        console_url=ctx.obj["console_url"],
        console_token=ctx.obj["console_token"],
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
        console_url=ctx.obj["console_url"],
        console_token=ctx.obj["console_token"],
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
        console_url=ctx.obj["console_url"],
        console_token=ctx.obj["console_token"],
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
        console_url=ctx.obj["console_url"],
        console_token=ctx.obj["console_token"],
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
        console_url=ctx.obj["console_url"],
        console_token=ctx.obj["console_token"],
        callback=lambda controller: controller.flash_internal_memory(
            verify=_resolve_verify(controller, verify),
            force=force,
        ),
    )
    if result is not None:
        _emit_result(result)


# ---------------------------------------------------------------------------
# relay-board <board> console …
# ---------------------------------------------------------------------------


def _console_board(ctx: click.Context) -> str:
    return Path(ctx.obj["config_path"]).stem


def _console_call(ctx: click.Context, callback):
    try:
        with _console_client(ctx.obj) as client:
            return callback(client)
    except ConsoleClientError as exc:
        raise click.ClickException(str(exc)) from exc


def _emit(payload: dict, as_json: bool, human) -> None:
    if as_json:
        click.echo(json_module.dumps(payload, indent=2))
    else:
        human(payload)


_json_option = click.option(
    "--json", "as_json", is_flag=True, default=False, help="Raw JSON output."
)


@board_cli.group("console")
@click.pass_context
def console_group(ctx: click.Context) -> None:
    """Serial console: boot logs, reports and named actions."""


@console_group.command("boots")
@click.option("--limit", default=10, show_default=True, type=click.IntRange(1, 200))
@_json_option
@click.pass_context
def console_boots(ctx: click.Context, limit: int, as_json: bool) -> None:
    """List the newest boots."""

    board = _console_board(ctx)
    payload = _console_call(ctx, lambda c: c.boots(board, limit=limit))

    def _human(data: dict) -> None:
        for boot in data.get("boots", []):
            trigger = boot.get("trigger", "")
            if boot.get("reason"):
                trigger += f": {boot['reason']}"
            if boot.get("actor"):
                trigger += f" by {boot['actor']}"
            click.echo(
                f"boot {boot['id']:>6}  {boot.get('started_at', '')}  "
                f"{boot.get('result', ''):<14} {trigger}"
            )

    _emit(payload, as_json, _human)


@console_group.command("log")
@click.option("--boot", default="last", show_default=True)
@click.option("--tail", default=200, show_default=True, type=click.IntRange(1, 2000))
@click.option("--grep", default=None, help="Regex filter.")
@_json_option
@click.pass_context
def console_log(
    ctx: click.Context, boot: str, tail: int, grep: str | None, as_json: bool
) -> None:
    """Show bounded lines of one boot's log."""

    board = _console_board(ctx)
    payload = _console_call(
        ctx, lambda c: c.log(board, boot=boot, tail=tail, grep=grep)
    )

    def _human(data: dict) -> None:
        for line in data.get("untrusted_lines", []):
            click.echo(f"{line['timestamp']}  {line['text']}")

    _emit(payload, as_json, _human)


@console_group.command("report")
@click.option("--boot", default="last", show_default=True)
@_json_option
@click.pass_context
def console_report(ctx: click.Context, boot: str, as_json: bool) -> None:
    """Show the parsed boot report."""

    board = _console_board(ctx)
    payload = _console_call(ctx, lambda c: c.report(board, boot=boot))

    def _human(data: dict) -> None:
        click.echo(f"boot {data['boot']} ({data['trigger']}): {data['result']}")
        if data.get("uboot"):
            click.echo(f"  u-boot: {data['uboot']}")
        if data.get("kernel"):
            click.echo(f"  kernel: {data['kernel']}")
        for name, value in sorted(data.get("times_s", {}).items()):
            click.echo(f"  time {name}: {value}s")
        click.echo(
            f"  errors: {data.get('error_count', 0)}  "
            f"warnings: {data.get('warning_count', 0)}  "
            f"lines: {data.get('log_lines', 0)}"
        )
        for error in data.get("errors", []):
            click.echo(f"  ERROR line {error['line']}: {error['text']}")
        if data.get("addresses_seen"):
            click.echo("  addresses seen: " + ", ".join(data["addresses_seen"]))

    _emit(payload, as_json, _human)


@console_group.command("wait")
@click.option("--pattern", default="login:", show_default=True)
@click.option(
    "--timeout", "timeout_s", default=120, show_default=True,
    type=click.IntRange(1, 600),
)
@_json_option
@click.pass_context
def console_wait(
    ctx: click.Context, pattern: str, timeout_s: int, as_json: bool
) -> None:
    """Block until a console line matches a pattern."""

    board = _console_board(ctx)
    payload = _console_call(
        ctx, lambda c: c.wait(board, pattern=pattern, timeout_s=timeout_s)
    )

    def _human(data: dict) -> None:
        if data.get("matched"):
            click.echo(
                f"matched after {data['elapsed_s']}s: {data.get('match', '')}"
            )
        else:
            click.echo(f"timeout after {data['elapsed_s']}s; last lines:")
            for line in data.get("untrusted_lines", []):
                click.echo(f"  {line}")

    _emit(payload, as_json, _human)


@console_group.command("state")
@_json_option
@click.pass_context
def console_state(ctx: click.Context, as_json: bool) -> None:
    """Probe the console state (sends one carriage return)."""

    board = _console_board(ctx)
    payload = _console_call(ctx, lambda c: c.state(board))

    def _human(data: dict) -> None:
        click.echo(f"state: {data['state']}")
        for line in data.get("untrusted_lines", []):
            click.echo(f"  {line}")

    _emit(payload, as_json, _human)


@console_group.command("ip")
@_json_option
@click.pass_context
def console_ip(ctx: click.Context, as_json: bool) -> None:
    """Read the DUT's IPv4 addresses via the ip_address action."""

    board = _console_board(ctx)
    payload = _console_call(ctx, lambda c: c.run_action(board, "ip_address"))

    def _human(data: dict) -> None:
        if data.get("error"):
            click.echo(f"{data['error']}: {data.get('message', '')}")
            return
        parsed = data.get("parsed") or []
        if not parsed:
            click.echo("no addresses found")
        for entry in parsed:
            click.echo(
                f"{entry['interface']}: {entry['address']}/{entry['prefix']}"
            )

    _emit(payload, as_json, _human)


@console_group.command("actions")
@_json_option
@click.pass_context
def console_actions(ctx: click.Context, as_json: bool) -> None:
    """List the board's named console actions."""

    board = _console_board(ctx)
    payload = _console_call(ctx, lambda c: c.actions(board))

    def _human(data: dict) -> None:
        for action in data.get("actions", []):
            flags = " [mutating]" if action.get("mutating") else ""
            click.echo(
                f"{action['name']:<20} ({action['context']}){flags}  "
                f"{action.get('description') or ''}"
            )

    _emit(payload, as_json, _human)


@console_group.command("run")
@click.argument("action")
@_json_option
@click.pass_context
def console_run(ctx: click.Context, action: str, as_json: bool) -> None:
    """Run one named console action."""

    board = _console_board(ctx)
    payload = _console_call(ctx, lambda c: c.run_action(board, action))

    def _human(data: dict) -> None:
        if data.get("error"):
            click.echo(f"{data['error']}: {data.get('message', '')}")
            return
        click.echo(
            f"{data['action']}: exit={data.get('exit_code')} "
            f"({data.get('duration_s')}s, state was {data.get('state_before')})"
        )
        click.echo(json_module.dumps(data.get("parsed"), indent=2))

    _emit(payload, as_json, _human)


@console_group.command("follow")
@click.pass_context
def console_follow(ctx: click.Context) -> None:
    """Stream the read-only live mirror (Ctrl-C to stop)."""

    board = _console_board(ctx)
    try:
        profile = load_board_profile(ctx.obj["config_path"])
    except BoardConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    if profile.console is None or profile.console.mirror_port is None:
        raise click.ClickException(
            f"board {board!r} has no console.mirror_port configured"
        )
    import urllib.parse

    host = urllib.parse.urlsplit(ctx.obj["console_url"]).hostname or "127.0.0.1"
    port = profile.console.mirror_port
    click.echo(f"connecting to {host}:{port} (read-only; Ctrl-C to stop)")
    try:
        with socket.create_connection((host, port), timeout=10) as sock:
            sock.settimeout(None)
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                sys.stdout.write(data.decode("utf-8", errors="replace"))
                sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        raise click.ClickException(f"mirror connection failed: {exc}") from exc


def main() -> None:  # pragma: no cover
    board_cli()
