"""relay-console: HTTP service that owns each board's serial console.

Run with::

    relay-console [--boards-dir DIR] [--host HOST] [--port PORT]
                  [--data-dir DIR]

A bearer token is read from ``RELAY_CONSOLE_TOKEN``. It is required when
``--host`` is not loopback; the service refuses to start without it.
"""

from __future__ import annotations

import argparse
import hmac
import ipaddress
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel

from relay_tools.board_config import (
    DEFAULT_BOARD_CONFIG_DIR,
    BoardConfigError,
    BoardProfile,
    load_board_profile,
)

from .capture import BoardConsole
from .report import build_report
from .session import (
    ConsoleBusyError,
    ConsoleSession,
    ConsoleSessionError,
)

logger = logging.getLogger(__name__)

BOARDS_DIR_ENV = "RELAY_BOARDS_DIR"
TOKEN_ENV = "RELAY_CONSOLE_TOKEN"
DEFAULT_DATA_DIR = Path("/var/lib/relay/console")
DEFAULT_PORT = 8001
MAX_TAIL = 2000
MAX_GREP_MATCHES = 2000
MAX_WAIT_S = 600
BOOT_SELECTOR_RE = re.compile(r"^(last|[0-9]{1,9})$")


class ConsoleManager:
    """Holds one :class:`BoardConsole` per board with a console block."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.consoles: dict[str, BoardConsole] = {}
        self.profiles: dict[str, BoardProfile] = {}

    def add_board(self, name: str, profile: BoardProfile) -> BoardConsole:
        if profile.console is None:
            raise ValueError(f"board {name!r} has no console block")
        console = BoardConsole(
            name, profile.console, data_dir=self.data_dir
        )
        self.consoles[name] = console
        self.profiles[name] = profile
        return console

    def start(self) -> None:
        for console in self.consoles.values():
            console.start()

    def stop(self) -> None:
        for console in self.consoles.values():
            console.stop()

    def get(self, board: str) -> BoardConsole:
        console = self.consoles.get(board)
        if console is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"board {board!r} has no console capture; boards with a "
                    "console: " + (", ".join(sorted(self.consoles)) or "none")
                ),
            )
        return console


def load_console_boards(
    boards_dir: str | Path,
) -> dict[str, BoardProfile]:
    """Load profiles with a console block; check mirror-port uniqueness."""

    boards: dict[str, BoardProfile] = {}
    mirror_ports: dict[int, str] = {}
    for path in sorted(Path(boards_dir).glob("*.yaml")):
        try:
            profile = load_board_profile(path)
        except BoardConfigError as exc:
            logger.warning("skipping %s: %s", path, exc)
            continue
        if profile.console is None:
            continue
        port = profile.console.mirror_port
        if port is not None:
            if port in mirror_ports:
                raise BoardConfigError(
                    f"{path.name}: console.mirror_port {port} is already "
                    f"used by board {mirror_ports[port]!r}; mirror ports "
                    "must be unique across all loaded profiles."
                )
            mirror_ports[port] = path.stem
        boards[path.stem] = profile
    return boards


class MarkerRequest(BaseModel):
    reason: str
    actor: str
    audit_id: str | None = None


def create_app(
    manager: ConsoleManager, *, token: str | None = None
) -> FastAPI:
    app = FastAPI(title="relay-console", docs_url=None, redoc_url=None)
    app.state.manager = manager

    def _check_token(request: Request) -> None:
        if not token:
            return
        header = request.headers.get("authorization", "")
        scheme, _, credentials = header.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            credentials.strip().encode(), token.encode()
        ):
            raise HTTPException(status_code=401, detail="invalid token")

    auth = Depends(_check_token)

    @app.get("/consoles", dependencies=[auth])
    def list_consoles() -> dict[str, Any]:
        consoles = []
        for name in sorted(manager.consoles):
            console = manager.consoles[name]
            current = console.store.current
            consoles.append(
                {
                    "board": name,
                    "device": console.config.device,
                    "connected": console.connected,
                    "current_boot": current.id if current else None,
                    "mirror_port": console.config.mirror_port,
                }
            )
        return {"consoles": consoles}

    @app.post("/consoles/{board}/markers", dependencies=[auth])
    def post_marker(board: str, marker: MarkerRequest) -> dict[str, Any]:
        console = manager.get(board)
        record = console.post_marker(
            reason=marker.reason,
            actor=marker.actor,
            audit_id=marker.audit_id,
        )
        return {"board": board, "boot": record.id}

    @app.get("/consoles/{board}/boots", dependencies=[auth])
    def list_boots(
        board: str, limit: int = Query(default=10, ge=1, le=200)
    ) -> dict[str, Any]:
        console = manager.get(board)
        return {
            "board": board,
            "boots": [
                record.to_json() for record in console.store.records(limit)
            ],
        }

    def _find_boot(console: BoardConsole, boot: str):
        if not BOOT_SELECTOR_RE.match(boot):
            raise HTTPException(
                status_code=400,
                detail="boot must be 'last' or a decimal boot id",
            )
        record = console.store.find(boot)
        if record is None:
            raise HTTPException(
                status_code=404, detail=f"boot {boot!r} not found"
            )
        return record

    @app.get("/consoles/{board}/boots/{boot}/log", dependencies=[auth])
    def boot_log(
        board: str,
        boot: str,
        tail: int = Query(default=200, ge=1, le=MAX_TAIL),
        grep: str | None = Query(default=None, max_length=200),
        since_line: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        console = manager.get(board)
        record = _find_boot(console, boot)
        lines = console.store.read_log(record.id)
        if since_line:
            lines = [line for line in lines if line.number > since_line]
        if grep is not None:
            try:
                pattern = re.compile(grep)
            except re.error as exc:
                raise HTTPException(
                    status_code=400, detail=f"invalid grep regex: {exc}"
                ) from exc
            lines = [line for line in lines if pattern.search(line.text)][
                :MAX_GREP_MATCHES
            ]
        lines = lines[-tail:]
        return {
            "board": board,
            "boot": record.id,
            "total_lines": record.lines,
            "untrusted_lines": [
                {
                    "line": line.number,
                    "timestamp": line.timestamp.isoformat(),
                    "text": line.text,
                }
                for line in lines
            ],
        }

    @app.get("/consoles/{board}/boots/{boot}/report", dependencies=[auth])
    def boot_report(board: str, boot: str) -> dict[str, Any]:
        console = manager.get(board)
        record = _find_boot(console, boot)
        current = console.store.current
        return build_report(
            console.store.read_log(record.id),
            record,
            config=console.config,
            board=board,
            is_current=current is not None and current.id == record.id,
        )

    @app.get("/consoles/{board}/wait", dependencies=[auth])
    def wait(
        board: str,
        pattern: str = Query(..., max_length=200),
        timeout_s: float = Query(default=120, gt=0, le=MAX_WAIT_S),
    ) -> dict[str, Any]:
        console = manager.get(board)
        try:
            re.compile(pattern)
        except re.error as exc:
            raise HTTPException(
                status_code=400, detail=f"invalid pattern regex: {exc}"
            ) from exc
        result = console.wait_for_line(pattern, timeout_s=timeout_s)
        result["board"] = board
        return result

    @app.get("/consoles/{board}/actions", dependencies=[auth])
    def list_actions(board: str) -> dict[str, Any]:
        console = manager.get(board)
        return {
            "board": board,
            "actions": [
                {
                    "name": action.name,
                    "context": action.context,
                    "description": action.description,
                    "parse": action.parse,
                    "mutating": action.mutating,
                }
                for action in sorted(
                    console.config.actions.values(),
                    key=lambda action: action.name,
                )
            ],
        }

    @app.post("/consoles/{board}/state", dependencies=[auth])
    def probe_state(board: str) -> dict[str, Any]:
        console = manager.get(board)
        session = ConsoleSession(console)
        try:
            state = session.probe_state()
        except ConsoleBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "board": board,
            "state": state,
            "untrusted_lines": console.recent_lines(10),
        }

    @app.post("/consoles/{board}/actions/{name}", dependencies=[auth])
    def run_action(
        board: str, name: str, request: Request
    ) -> dict[str, Any]:
        console = manager.get(board)
        action = console.config.actions.get(name)
        if action is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"unknown console action {name!r}; available: "
                    + ", ".join(sorted(console.config.actions))
                ),
            )
        actor = request.headers.get("x-relay-actor", "unknown")
        audit_id = request.headers.get("x-relay-audit-id")
        session = ConsoleSession(console)
        try:
            return session.run_action(action, actor=actor, audit_id=audit_id)
        except ConsoleBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ConsoleSessionError as exc:
            return {
                "board": board,
                "action": name,
                "error": exc.code,
                "message": str(exc),
                **exc.details,
            }

    return app


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="relay-console",
        description="Serial console capture service for boards under test.",
    )
    parser.add_argument(
        "--boards-dir",
        type=Path,
        default=Path(
            os.environ.get(BOARDS_DIR_ENV, str(DEFAULT_BOARD_CONFIG_DIR))
        ),
        help=f"Directory with board profile YAML files (env {BOARDS_DIR_ENV}).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host.")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="Bind port."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory for boot logs and indexes.",
    )
    return parser


def _is_loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    token = os.environ.get(TOKEN_ENV) or None
    if not _is_loopback(args.host) and not token:
        print(
            f"relay-console: refusing to bind {args.host} without a bearer "
            f"token; set {TOKEN_ENV}.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    try:
        boards = load_console_boards(args.boards_dir)
    except BoardConfigError as exc:
        print(f"relay-console: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    manager = ConsoleManager(args.data_dir)
    for name, profile in boards.items():
        manager.add_board(name, profile)
    if not manager.consoles:
        logger.warning(
            "no board profile in %s has a console block", args.boards_dir
        )
    app = create_app(manager, token=token)
    manager.start()
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        manager.stop()


if __name__ == "__main__":
    main()
