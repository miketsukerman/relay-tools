"""MCP server exposing board-level relay actions to AI agents.

``relay-mcp`` is a thin layer over :class:`relay_tools.board.BoardController`.
It never imports GPIO code: it talks to the relay daemon over HTTP only, so it
can run on the Pi or on any host that can reach the daemon.

Only the named actions from a board profile in ``--config-dir`` are exposed.
Raw channel tools, ``force=True`` and risky boot modes are never available to
the model.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

from relay_tools.board import (
    BoardController,
    BoardExecutionError,
    BoardStatus,
    WorkflowResult,
)
from relay_tools.board_config import (
    DEFAULT_BOARD_CONFIG_DIR,
    BoardConfigError,
    BoardProfile,
    load_board_profile,
    resolve_default_board_config_path,
)
from relay_tools.client import (
    DEFAULT_URL,
    RelayClient,
    RelayConnectionError,
    RelayRequestError,
)
from relay_tools.mcp.audit import AUDIT_LOG_ENV, DEFAULT_AUDIT_LOG, AuditLog

try:  # mcp >= 2.0: FastMCP was renamed to MCPServer.
    from mcp.server.mcpserver import MCPServer as _FastMCP
    from mcp.server.mcpserver.exceptions import ToolError

    _MCP_V2 = True
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _FastMCP
    from mcp.server.fastmcp.exceptions import ToolError

    _MCP_V2 = False

from mcp.types import ToolAnnotations

logger = logging.getLogger(__name__)

NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
POWER_TOOLS = frozenset(
    {"power_on", "power_off", "power_cycle", "reset", "boot_and_wait"}
)
DEFAULT_COOLDOWN = 5.0
COOLDOWN_ENV = "RELAY_MCP_COOLDOWN"
READ_ONLY_ENV = "RELAY_MCP_READ_ONLY"
URL_ENV = "RELAY_API_URL"
TOKEN_ENV = "RELAY_API_TOKEN"


def _tool_annotations(
    *,
    read_only: bool = False,
    destructive: bool = False,
    idempotent: bool = False,
) -> ToolAnnotations:
    """Build ToolAnnotations for both MCP SDK generations.

    mcp 2.x uses snake_case field names, mcp 1.x uses camelCase.
    """

    model_fields = getattr(ToolAnnotations, "model_fields", {})
    if "read_only_hint" in model_fields:
        return ToolAnnotations(
            read_only_hint=read_only,
            destructive_hint=destructive,
            idempotent_hint=idempotent,
            open_world_hint=False,
        )
    return ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=False,
    )


def _validate_name(value: str, kind: str) -> str:
    if not isinstance(value, str) or not NAME_PATTERN.match(value):
        raise ToolError(
            f"invalid {kind} name {value!r}: it must match "
            "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$ (no path separators or '..')"
        )
    return value


@dataclass
class Settings:
    """Runtime configuration for the relay-mcp server."""

    url: str = DEFAULT_URL
    config_dir: Path = DEFAULT_BOARD_CONFIG_DIR
    api_token: str | None = None
    cooldown: float = DEFAULT_COOLDOWN
    audit_log: Path = DEFAULT_AUDIT_LOG
    read_only: bool = False
    allow_boards: tuple[str, ...] = ()
    client_factory: Callable[..., httpx.Client] = field(default=httpx.Client)


def _status_payload(status: BoardStatus) -> dict[str, Any]:
    return {
        "signals": dict(status.signals),
        "switches": dict(status.switches),
        "matching_boot_modes": list(status.matching_boot_modes),
    }


class RelayMCPService:
    """Implements the relay-mcp tools; one instance per server process."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.audit = AuditLog(settings.audit_log)
        self.clock: Callable[[], float] = time.monotonic
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._last_power_action: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Board resolution and profile loading
    # ------------------------------------------------------------------

    def _board_allowed(self, name: str) -> bool:
        allow = self.settings.allow_boards
        return not allow or name in allow

    def _resolve_board_path(self, board: str | None) -> Path:
        config_dir = Path(self.settings.config_dir)
        if board is not None:
            _validate_name(board, "board")
            if not self._board_allowed(board):
                raise ToolError(
                    f"board {board!r} is not in the list of allowed boards"
                )
            path = config_dir / f"{board}.yaml"
            if not path.exists():
                raise ToolError(
                    f"unknown board {board!r}; available boards: "
                    + (", ".join(self._board_names()) or "none")
                )
            return path
        try:
            path = Path(
                resolve_default_board_config_path(config_dir=config_dir)
            )
        except ValueError as exc:
            raise ToolError(
                f"{exc} Alternatively pass the 'board' argument."
            ) from exc
        if not self._board_allowed(path.stem):
            raise ToolError(
                f"board {path.stem!r} is not in the list of allowed boards"
            )
        return path

    def _board_names(self) -> list[str]:
        config_dir = Path(self.settings.config_dir)
        names = sorted(
            path.stem
            for path in config_dir.glob("*.yaml")
            if path.is_file()
        )
        return [name for name in names if self._board_allowed(name)]

    def _load_profile(self, path: Path) -> BoardProfile:
        try:
            return load_board_profile(path)
        except BoardConfigError as exc:
            raise ToolError(f"invalid board profile {path.name}: {exc}") from exc

    def _relay_client(self) -> RelayClient:
        token = self.settings.api_token
        factory = self.settings.client_factory

        def _make(**kwargs: Any) -> httpx.Client:
            if token:
                headers = dict(kwargs.pop("headers", None) or {})
                headers["Authorization"] = "Bearer " + token
                kwargs["headers"] = headers
            return factory(**kwargs)

        return RelayClient(self.settings.url, client_factory=_make)

    # ------------------------------------------------------------------
    # Safety helpers
    # ------------------------------------------------------------------

    def _board_lock(self, board: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(board, threading.Lock())

    def _refuse_read_only(self, tool: str) -> None:
        if self.settings.read_only:
            raise ToolError(
                f"{tool} refused: relay-mcp is running in read-only mode"
            )

    def _check_cooldown(self, board: str) -> None:
        last = self._last_power_action.get(board)
        if last is None:
            return
        remaining = self.settings.cooldown - (self.clock() - last)
        if remaining > 0:
            raise ToolError(
                f"power action on board {board!r} refused: cooldown active, "
                f"{remaining:.1f} seconds remaining"
            )

    def _assert_boot_mode_safe(self, profile: BoardProfile, mode: str) -> None:
        _validate_name(mode, "boot mode")
        if mode not in profile.boot_modes:
            raise ToolError(
                f"unknown boot mode {mode!r}; available modes: "
                + (", ".join(sorted(profile.boot_modes)) or "none")
            )
        if profile.boot_modes[mode].risky:
            raise ToolError(
                f"boot mode {mode!r} is marked risky and cannot be set by an "
                "agent; a human must run "
                f"`relay-board set-boot-mode {mode} --force` at the bench"
            )

    def _assert_workflow_safe(self, profile: BoardProfile, workflow: str) -> None:
        for step in profile.workflows.get(workflow, ()):
            if step.action != "set-boot-mode" or step.boot_mode is None:
                continue
            mode = profile.boot_modes.get(step.boot_mode)
            if mode is not None and mode.risky:
                raise ToolError(
                    f"workflow {workflow!r} refused: step {step.name!r} sets "
                    f"risky boot mode {step.boot_mode!r}; a human must run "
                    f"`relay-board run {workflow} --force` at the bench"
                )

    def _map_error(self, exc: Exception) -> ToolError:
        if isinstance(exc, BoardExecutionError):
            return ToolError(str(exc))
        if isinstance(exc, RelayConnectionError):
            return ToolError(
                f"relay daemon unreachable at {self.settings.url}; "
                "is `relay serve` running on the Pi?"
            )
        if isinstance(exc, (RelayRequestError, BoardConfigError)):
            return ToolError(str(exc))
        raise exc

    # ------------------------------------------------------------------
    # Action runner
    # ------------------------------------------------------------------

    def _run_action(
        self,
        *,
        tool: str,
        board: str | None,
        arguments: dict[str, Any],
        runner: Callable[[BoardController, BoardProfile, bool], WorkflowResult],
        precheck: Callable[[BoardProfile], None] | None = None,
    ) -> dict[str, Any]:
        self._refuse_read_only(tool)
        path = self._resolve_board_path(board)
        profile = self._load_profile(path)
        board_name = path.stem
        if precheck is not None:
            precheck(profile)
        lock = self._board_lock(board_name)
        if not lock.acquire(blocking=False):
            raise ToolError(
                f"board {board_name!r} is busy with another action; "
                "retry once it completes"
            )
        try:
            if tool in POWER_TOOLS:
                self._check_cooldown(board_name)
            verify = profile.defaults.verify
            try:
                with self._relay_client() as client:
                    controller = BoardController(profile, client)
                    result = runner(controller, profile, verify)
            except Exception as exc:  # noqa: BLE001 - mapped below
                if tool in POWER_TOOLS:
                    self._last_power_action[board_name] = self.clock()
                self.audit.record(
                    board=board_name,
                    tool=tool,
                    arguments=arguments,
                    ok=False,
                    error=str(exc),
                )
                raise self._map_error(exc) from exc
            if tool in POWER_TOOLS:
                self._last_power_action[board_name] = self.clock()
            self.audit.record(
                board=board_name,
                tool=tool,
                arguments=arguments,
                ok=True,
                steps=[step.name for step in result.steps],
                matching_boot_modes=list(
                    result.final_status.matching_boot_modes
                ),
            )
            return {
                "board": board_name,
                "action": tool,
                "ok": True,
                "steps": [
                    {"name": step.name, "detail": step.detail}
                    for step in result.steps
                ],
                "final_status": _status_payload(result.final_status),
            }
        finally:
            lock.release()

    # ------------------------------------------------------------------
    # Read-only tools
    # ------------------------------------------------------------------

    def list_boards(self) -> dict[str, Any]:
        config_dir = Path(self.settings.config_dir)
        boards: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for path in sorted(config_dir.glob("*.yaml")):
            if not path.is_file() or not self._board_allowed(path.stem):
                continue
            try:
                profile = load_board_profile(path)
            except BoardConfigError as exc:
                errors.append({"name": path.stem, "error": str(exc)})
                continue
            description = None
            if profile.defaults.power_switch:
                description = (
                    f"power switch {profile.defaults.power_switch!r}"
                )
            boards.append(
                {
                    "name": path.stem,
                    "profile_name": profile.name,
                    "description": description,
                    "boot_modes": sorted(profile.boot_modes),
                    "workflows": sorted(profile.workflows),
                }
            )
        return {"config_dir": str(config_dir), "boards": boards, "errors": errors}

    def describe_board(self, board: str | None = None) -> dict[str, Any]:
        path = self._resolve_board_path(board)
        profile = self._load_profile(path)
        return {
            "board": path.stem,
            "name": profile.name,
            "signals": [
                {
                    "name": signal.name,
                    "channel": signal.channel,
                    "description": signal.description,
                }
                for signal in profile.signals.values()
            ],
            "switches": [
                {
                    "name": switch.name,
                    "channel": switch.channel,
                    "description": switch.description,
                }
                for switch in profile.switches.values()
            ],
            "boot_modes": [
                {
                    "name": mode.name,
                    "description": mode.description,
                    "risky": mode.risky,
                }
                for mode in profile.boot_modes.values()
            ],
            "workflows": sorted(profile.workflows),
            "timings": dict(profile.timings),
            "defaults": {
                "power_signal": profile.defaults.power_signal,
                "power_switch": profile.defaults.power_switch,
                "reset_signal": profile.defaults.reset_signal,
                "verify": profile.defaults.verify,
            },
        }

    def board_status(self, board: str | None = None) -> dict[str, Any]:
        path = self._resolve_board_path(board)
        profile = self._load_profile(path)
        try:
            with self._relay_client() as client:
                status = BoardController(profile, client).status()
        except Exception as exc:  # noqa: BLE001 - mapped below
            raise self._map_error(exc) from exc
        payload = _status_payload(status)
        payload["board"] = path.stem
        payload["channels"] = {
            str(channel): on
            for channel, on in sorted(status.relay_state.by_channel.items())
        }
        return payload

    def audit_log(self, limit: int = 20) -> dict[str, Any]:
        return {"entries": self.audit.tail(limit)}

    # ------------------------------------------------------------------
    # Action tools
    # ------------------------------------------------------------------

    def power_on(self, board: str | None = None) -> dict[str, Any]:
        return self._run_action(
            tool="power_on",
            board=board,
            arguments={"board": board},
            runner=lambda ctl, profile, verify: ctl.power_on(verify=verify),
            precheck=lambda profile: self._assert_workflow_safe(
                profile, "power-on"
            ),
        )

    def power_off(self, board: str | None = None) -> dict[str, Any]:
        return self._run_action(
            tool="power_off",
            board=board,
            arguments={"board": board},
            runner=lambda ctl, profile, verify: ctl.power_off(verify=verify),
            precheck=lambda profile: self._assert_workflow_safe(
                profile, "power-off"
            ),
        )

    def power_cycle(self, board: str | None = None) -> dict[str, Any]:
        return self._run_action(
            tool="power_cycle",
            board=board,
            arguments={"board": board},
            runner=lambda ctl, profile, verify: ctl.power_cycle(verify=verify),
            precheck=lambda profile: self._assert_workflow_safe(
                profile, "power-cycle"
            ),
        )

    def reset(self, board: str | None = None) -> dict[str, Any]:
        return self._run_action(
            tool="reset",
            board=board,
            arguments={"board": board},
            runner=lambda ctl, profile, verify: ctl.reset(verify=verify),
            precheck=lambda profile: self._assert_workflow_safe(
                profile, "reset"
            ),
        )

    def set_boot_mode(self, mode: str, board: str | None = None) -> dict[str, Any]:
        def _precheck(profile: BoardProfile) -> None:
            self._assert_boot_mode_safe(profile, mode)

        return self._run_action(
            tool="set_boot_mode",
            board=board,
            arguments={"board": board, "mode": mode},
            runner=lambda ctl, profile, verify: ctl.set_boot_mode(
                mode, verify=verify
            ),
            precheck=_precheck,
        )

    def boot_and_wait(self, mode: str, board: str | None = None) -> dict[str, Any]:
        def _precheck(profile: BoardProfile) -> None:
            self._assert_boot_mode_safe(profile, mode)
            self._assert_workflow_safe(profile, "boot-and-wait")
            self._assert_workflow_safe(profile, "power-cycle")

        return self._run_action(
            tool="boot_and_wait",
            board=board,
            arguments={"board": board, "mode": mode},
            runner=lambda ctl, profile, verify: ctl.boot_and_wait(
                mode, verify=verify
            ),
            precheck=_precheck,
        )

    def run_workflow(self, workflow: str, board: str | None = None) -> dict[str, Any]:
        def _precheck(profile: BoardProfile) -> None:
            _validate_name(workflow, "workflow")
            if workflow not in profile.workflows:
                raise ToolError(
                    f"unknown workflow {workflow!r}; available workflows: "
                    + (", ".join(sorted(profile.workflows)) or "none")
                )
            self._assert_workflow_safe(profile, workflow)

        return self._run_action(
            tool="run_workflow",
            board=board,
            arguments={"board": board, "workflow": workflow},
            runner=lambda ctl, profile, verify: ctl.run_workflow(
                workflow, verify=verify
            ),
            precheck=_precheck,
        )


def create_server(settings: Settings) -> Any:
    """Build the MCP server with the relay-mcp tools and prompts."""

    service = RelayMCPService(settings)
    mcp_server = _FastMCP(
        name="relay-mcp",
        instructions=(
            "Control the power and boot mode of a device under test wired "
            "to a relay board. Only the named, verified actions from the "
            "board profile are available; risky boot modes need a human at "
            "the bench."
        ),
    )
    mcp_server.relay_service = service  # type: ignore[attr-defined]

    read_only = _tool_annotations(read_only=True, idempotent=True)
    action = _tool_annotations()
    destructive = _tool_annotations(destructive=True)

    @mcp_server.tool(annotations=read_only)
    def list_boards() -> dict:
        """List the board profiles available in the config directory."""
        return service.list_boards()

    @mcp_server.tool(annotations=read_only)
    def describe_board(board: str | None = None) -> dict:
        """Describe a board profile: signals, switches, boot modes,
        workflows, timings and defaults. Does not contact the daemon."""
        return service.describe_board(board)

    @mcp_server.tool(annotations=read_only)
    def board_status(board: str | None = None) -> dict:
        """Read the live board state: signals, switches, matching boot
        modes and raw relay channel states."""
        return service.board_status(board)

    @mcp_server.tool(annotations=action)
    def power_on(board: str | None = None) -> dict:
        """Power the board on using the profile's power workflow/switch."""
        return service.power_on(board)

    @mcp_server.tool(annotations=destructive)
    def power_off(board: str | None = None) -> dict:
        """Power the board off. Destructive: cuts power to the DUT."""
        return service.power_off(board)

    @mcp_server.tool(annotations=action)
    def power_cycle(board: str | None = None) -> dict:
        """Power-cycle the board (off, settle delay, on)."""
        return service.power_cycle(board)

    @mcp_server.tool(annotations=action)
    def reset(board: str | None = None) -> dict:
        """Pulse the board's reset signal."""
        return service.reset(board)

    @mcp_server.tool(annotations=action)
    def set_boot_mode(mode: str, board: str | None = None) -> dict:
        """Set the board's boot mode strap signals/switches.
        Risky modes are refused; a human must use relay-board --force."""
        return service.set_boot_mode(mode, board)

    @mcp_server.tool(annotations=action)
    def boot_and_wait(mode: str, board: str | None = None) -> dict:
        """Set a boot mode, power-cycle and wait for the profile's
        boot_wait. Blocks until the board had time to boot."""
        return service.boot_and_wait(mode, board)

    @mcp_server.tool(annotations=action)
    def run_workflow(workflow: str, board: str | None = None) -> dict:
        """Run a named workflow from the board profile."""
        return service.run_workflow(workflow, board)

    @mcp_server.tool(annotations=read_only)
    def audit_log(limit: int = 20) -> dict:
        """Return the newest relay-mcp audit entries (limit 1..200)."""
        return service.audit_log(limit)

    @mcp_server.prompt()
    def smoke_test() -> str:
        """Check what relay-mcp can see without changing any relay state."""
        return (
            "Run a read-only smoke test of the relay-mcp server. Call "
            "list_boards, then describe_board and board_status for each "
            "available board. Do not call any tool that changes relay "
            "state. Report the boards, their signals, switches, boot modes "
            "and current status."
        )

    @mcp_server.prompt()
    def power_cycle_and_check(board: str) -> str:
        """Power-cycle a board and confirm it came back up."""
        return (
            f"Power-cycle the board {board!r} and verify it. First call "
            f"board_status(board={board!r}) and note the state. Then call "
            f"power_cycle(board={board!r}). Finally call "
            f"board_status(board={board!r}) again and confirm the power "
            "switch ended active. Report each step."
        )

    @mcp_server.prompt()
    def boot_from(board: str, mode: str) -> str:
        """Boot a board from a given boot mode and verify it."""
        return (
            f"Boot the board {board!r} from boot mode {mode!r}. Call "
            f"describe_board(board={board!r}) to check that the mode exists "
            f"and is not risky. Then call set_boot_mode(mode={mode!r}, "
            f"board={board!r}), power_cycle(board={board!r}) and "
            f"board_status(board={board!r}). Confirm {mode!r} is listed in "
            "matching_boot_modes and report the result."
        )

    return mcp_server


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="relay-mcp",
        description="MCP server for board-level relay control.",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get(URL_ENV, DEFAULT_URL),
        help=f"Relay daemon URL (env {URL_ENV}).",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=DEFAULT_BOARD_CONFIG_DIR,
        help="Directory with board profile YAML files.",
    )
    parser.add_argument(
        "--api-token",
        default=os.environ.get(TOKEN_ENV),
        help=(
            f"Optional bearer token sent on every daemon request (env {TOKEN_ENV})."
        ),
    )
    parser.add_argument(
        "--cooldown",
        type=float,
        default=float(os.environ.get(COOLDOWN_ENV, DEFAULT_COOLDOWN)),
        help=(
            "Seconds between power actions on the same board "
            f"(env {COOLDOWN_ENV})."
        ),
    )
    parser.add_argument(
        "--audit-log",
        type=Path,
        default=Path(os.environ.get(AUDIT_LOG_ENV, str(DEFAULT_AUDIT_LOG))),
        help=f"Audit log JSONL path (env {AUDIT_LOG_ENV}).",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        default=os.environ.get(READ_ONLY_ENV, "") == "1",
        help=(
            "Refuse every tool that changes relay state "
            f"(env {READ_ONLY_ENV}=1)."
        ),
    )
    parser.add_argument(
        "--allow-board",
        action="append",
        default=[],
        metavar="NAME",
        help="Limit the exposed boards (repeatable).",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="MCP transport (default stdio).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host.")
    parser.add_argument(
        "--port", type=int, default=8766, help="HTTP bind port."
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings(
        url=args.url,
        config_dir=args.config_dir,
        api_token=args.api_token,
        cooldown=args.cooldown,
        audit_log=args.audit_log,
        read_only=args.read_only,
        allow_boards=tuple(args.allow_board),
    )
    server = create_server(settings)
    logger.info(
        "relay-mcp: daemon=%s config-dir=%s read-only=%s",
        settings.url,
        settings.config_dir,
        settings.read_only,
    )
    if args.transport == "http":
        if _MCP_V2:
            server.run(
                transport="streamable-http", host=args.host, port=args.port
            )
        else:
            server.settings.host = args.host
            server.settings.port = args.port
            server.run(transport="streamable-http")
    else:
        server.run()


if __name__ == "__main__":
    main()
