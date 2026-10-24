"""Console state detection, login and named action execution.

The only bytes ever written to the serial port are: one carriage return for
the state probe, the login sequence, the commands of named actions, and an
``exit`` after an action when ``logout_after`` is set.
"""

from __future__ import annotations

import os
import re
import secrets
import stat
import time
from typing import Any, Callable

from relay_tools.board_config import ConsoleActionConfig, ConsoleConfig

from .capture import BoardConsole, ConsoleUnavailableError, Tap
from .parsers import parse_output

PROBE_WINDOW_S = 2.0
ACTION_LOCK_TIMEOUT_S = 10.0
MAX_OUTPUT_LINES = 200
DEFAULT_LOGIN_PROMPT = r"login:"
DEFAULT_UBOOT_PROMPT = r"=> $"
DEFAULT_SHELL_PROMPT = r"[#$] $"
_PASSWORD_PROMPT_RE = re.compile(r"[Pp]assword:")
_LOGIN_FAILED_RE = re.compile(r"Login incorrect|Authentication failure")
_KERNEL_LOG_RE = re.compile(r"^\[ *[0-9]+\.[0-9]+\]")

STATE_LINUX_SHELL = "linux_shell"
STATE_LINUX_LOGIN = "linux_login"
STATE_UBOOT = "uboot"
STATE_BOOTING = "booting"
STATE_SILENT = "silent"
STATE_DISCONNECTED = "disconnected"


class ConsoleBusyError(RuntimeError):
    """Raised when another action already holds the board's console lock."""


class ConsoleSessionError(RuntimeError):
    """Structured refusal: the console is not in the state the action needs."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def read_password(config: ConsoleConfig) -> str | None:
    """Read the console password at login time, never at parse time."""

    login = config.login
    if login is None:
        return None
    if login.password_env:
        value = os.environ.get(login.password_env)
        if value is None:
            raise ConsoleSessionError(
                "login_failed",
                f"environment variable {login.password_env!r} is not set",
            )
        return value
    if login.password_file:
        try:
            status = os.stat(login.password_file)
        except FileNotFoundError as exc:
            raise ConsoleSessionError(
                "login_failed",
                f"password file {login.password_file} does not exist",
            ) from exc
        if stat.S_IMODE(status.st_mode) & 0o137:
            raise ConsoleSessionError(
                "login_failed",
                f"password file {login.password_file} is readable by others; "
                "tighten its mode to 0640 or stricter",
            )
        with open(login.password_file, encoding="utf-8") as handle:
            return handle.read().strip()
    return None


class _TapReader:
    """Accumulates sanitized stream text from a tap with a deadline."""

    def __init__(self, tap: Tap) -> None:
        self.tap = tap
        self.text = ""

    def gather(
        self,
        duration_s: float,
        *,
        until: Callable[[str], bool] | None = None,
    ) -> bool:
        """Collect chunks for ``duration_s``; stop early when ``until`` hits."""

        deadline = time.monotonic() + duration_s
        while True:
            if until is not None and until(self.text):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return until is not None and until(self.text)
            try:
                self.text += self.tap.chunks.get(timeout=min(remaining, 0.1))
            except Exception:  # noqa: BLE001 - queue.Empty
                continue

    @property
    def tail(self) -> str:
        """Text after the last newline: the current partial line."""

        return self.text.rsplit("\n", 1)[-1]

    def complete_lines(self) -> list[str]:
        return self.text.split("\n")[:-1]


class ConsoleSession:
    """Runs state probes and named actions on one board's console."""

    def __init__(self, console: BoardConsole) -> None:
        self.console = console
        self.config: ConsoleConfig = console.config

    # ------------------------------------------------------------------
    # State probe
    # ------------------------------------------------------------------

    def _prompt_res(self) -> dict[str, re.Pattern[str]]:
        login = self.config.login
        return {
            STATE_LINUX_SHELL: re.compile(
                login.shell_prompt if login else DEFAULT_SHELL_PROMPT
            ),
            STATE_LINUX_LOGIN: re.compile(
                self.config.login_prompt or DEFAULT_LOGIN_PROMPT
            ),
            STATE_UBOOT: re.compile(
                self.config.uboot_prompt or DEFAULT_UBOOT_PROMPT
            ),
        }

    def _match_state(self, reader: _TapReader) -> str | None:
        prompts = self._prompt_res()
        segments = reader.text.split("\n")
        for state in (STATE_LINUX_SHELL, STATE_UBOOT, STATE_LINUX_LOGIN):
            pattern = prompts[state]
            if any(pattern.search(segment) for segment in segments):
                return state
        return None

    def probe_state(self) -> str:
        """Take the console lock, send one CR and classify the response."""

        if not self._acquire():
            raise ConsoleBusyError(
                f"console of board {self.console.board!r} is busy with "
                "another action"
            )
        try:
            return self._probe_locked()
        finally:
            self._release()

    def _probe_locked(self) -> str:
        if not self.console.connected:
            return STATE_DISCONNECTED
        tap = self.console.open_tap()
        try:
            try:
                self.console.write(b"\r")
            except ConsoleUnavailableError:
                return STATE_DISCONNECTED
            reader = _TapReader(tap)
            matched = reader.gather(
                PROBE_WINDOW_S,
                until=lambda _: self._match_state(reader) is not None,
            )
            if matched:
                state = self._match_state(reader)
                if state is not None:
                    return state
            if reader.text.strip():
                return STATE_BOOTING
            return STATE_SILENT
        finally:
            self.console.close_tap(tap)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _acquire(self) -> bool:
        return self.console.action_lock.acquire(timeout=ACTION_LOCK_TIMEOUT_S)

    def _release(self) -> None:
        self.console.action_lock.release()

    def run_action(
        self,
        action: ConsoleActionConfig,
        *,
        actor: str = "relay-console",
        audit_id: str | None = None,
    ) -> dict[str, Any]:
        if not self._acquire():
            raise ConsoleBusyError(
                f"console of board {self.console.board!r} is busy with "
                "another action"
            )
        started = time.monotonic()
        try:
            state = self._probe_locked()
            self._check_context(action, state)
            logged_in = False
            if action.context == "linux" and state == STATE_LINUX_LOGIN:
                self._login()
                logged_in = True
            note = f"=== action {action.name} by {actor}"
            if audit_id:
                note += f" · audit {audit_id}"
            note += " ==="
            self.console.note(note)
            exit_code, output = self._run_command(action)
            parsed = parse_output(action.parse, output)
            if self.config.login and self.config.login.logout_after and (
                action.context == "linux"
            ):
                self.console.write(b"exit\n")
            return {
                "board": self.console.board,
                "action": action.name,
                "state_before": state,
                "logged_in": logged_in,
                "exit_code": exit_code,
                "parsed": parsed,
                "untrusted_output": output[:MAX_OUTPUT_LINES],
                "duration_s": round(time.monotonic() - started, 2),
            }
        finally:
            self._release()

    def _check_context(self, action: ConsoleActionConfig, state: str) -> None:
        if action.context == "linux":
            if state in (STATE_LINUX_SHELL, STATE_LINUX_LOGIN):
                return
            if state == STATE_UBOOT:
                raise ConsoleSessionError(
                    "wrong_context",
                    "board is at the U-Boot prompt; Linux actions are "
                    "unavailable",
                    state=state,
                )
        else:
            if state == STATE_UBOOT:
                return
            if state in (STATE_LINUX_SHELL, STATE_LINUX_LOGIN):
                raise ConsoleSessionError(
                    "wrong_context",
                    "board is in Linux; U-Boot actions are unavailable",
                    state=state,
                )
        if state == STATE_BOOTING:
            raise ConsoleSessionError(
                "wrong_context",
                "board is booting; use wait_for_console",
                state=state,
            )
        if state == STATE_DISCONNECTED:
            raise ConsoleSessionError(
                "disconnected",
                "console device is disconnected",
                state=state,
            )
        raise ConsoleSessionError(
            "wrong_context",
            f"console is {state}; cannot run {action.context} actions",
            state=state,
        )

    def _login(self) -> None:
        login = self.config.login
        if login is None:
            raise ConsoleSessionError(
                "login_failed",
                "board shows a login prompt but the profile has no "
                "console.login section",
            )
        password = read_password(self.config)
        if password:
            # Never let the password reach logs, results or the boot log.
            self.console.redactor.add_literal(password)
        shell_re = re.compile(login.shell_prompt)
        tap = self.console.open_tap()
        reader = _TapReader(tap)
        try:
            self.console.write(login.user.encode("utf-8") + b"\n")

            def _progress(text: str) -> bool:
                return bool(
                    shell_re.search(text.rsplit("\n", 1)[-1])
                    or _PASSWORD_PROMPT_RE.search(text)
                    or _LOGIN_FAILED_RE.search(text)
                )

            reader.gather(login.timeout_s, until=_progress)
            if _LOGIN_FAILED_RE.search(reader.text):
                raise ConsoleSessionError("login_failed", "login incorrect")
            if _PASSWORD_PROMPT_RE.search(reader.text):
                if password is None:
                    raise ConsoleSessionError(
                        "login_failed",
                        "board asked for a password but none is configured",
                    )
                reader.text = ""
                self.console.write(password.encode("utf-8") + b"\n")

                def _after_password(text: str) -> bool:
                    return bool(
                        shell_re.search(text.rsplit("\n", 1)[-1])
                        or _LOGIN_FAILED_RE.search(text)
                    )

                reader.gather(login.timeout_s, until=_after_password)
            if _LOGIN_FAILED_RE.search(reader.text):
                raise ConsoleSessionError("login_failed", "login incorrect")
            if not shell_re.search(reader.tail):
                raise ConsoleSessionError(
                    "login_failed",
                    "no shell prompt within "
                    f"{login.timeout_s:.0f}s after login",
                )
        finally:
            self.console.close_tap(tap)

    def _run_command(
        self, action: ConsoleActionConfig
    ) -> tuple[int | None, list[str]]:
        nonce = secrets.token_hex(8)
        begin = f"__RC_{nonce}_B__"
        end = f"__RC_{nonce}_E__"
        if action.context == "linux":
            command = f"echo {begin}; {action.command}; echo {end} $?\n"
        else:
            command = f"echo {begin}; {action.command}; echo {end}\n"
        tap = self.console.open_tap()
        reader = _TapReader(tap)
        try:
            self.console.write(command.encode("utf-8"))

            def _done(text: str) -> bool:
                for line in text.split("\n"):
                    stripped = line.strip()
                    if stripped.startswith(end):
                        return True
                return False

            finished = reader.gather(action.timeout_s, until=_done)
            lines = reader.complete_lines() + (
                [reader.tail] if reader.tail.strip() else []
            )
            output: list[str] = []
            exit_code: int | None = None
            capturing = False
            ended = False
            for line in lines:
                stripped = line.strip()
                if stripped == begin:
                    capturing = True
                    continue
                if stripped.startswith(end):
                    remainder = stripped[len(end):].strip()
                    if remainder.isdigit() or (
                        remainder.startswith("-") and remainder[1:].isdigit()
                    ):
                        exit_code = int(remainder)
                    ended = True
                    break
                if capturing:
                    if action.parse != "lines" and _KERNEL_LOG_RE.match(line):
                        continue
                    output.append(line)
            if not finished or not ended:
                raise ConsoleSessionError(
                    "timeout",
                    f"action {action.name!r} did not finish within "
                    f"{action.timeout_s:.0f}s",
                    untrusted_output=[
                        text for text in lines[-20:] if text.strip()
                    ],
                )
            return exit_code, output
        finally:
            self.console.close_tap(tap)
