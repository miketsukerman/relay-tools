"""Tests for console sessions: state probe, login and named actions."""

from __future__ import annotations

import json
import os
import re
import threading
import time

import pytest

pytest.importorskip("serial")

from relay_tools.board_config import (  # noqa: E402
    ConsoleActionConfig,
    ConsoleConfig,
    ConsoleLoginConfig,
)
from relay_tools.console import capture as capture_mod  # noqa: E402
from relay_tools.console import session as session_mod  # noqa: E402
from relay_tools.console.capture import BoardConsole  # noqa: E402
from relay_tools.console.session import (  # noqa: E402
    ConsoleBusyError,
    ConsoleSession,
    ConsoleSessionError,
    read_password,
)

IP_OUTPUT = (
    "2: eth0    inet 192.168.3.141/24 brd 192.168.3.255 scope global eth0\r\n"
    "3: wlan0    inet 10.0.0.7/16 scope global wlan0\r\n"
)

COMMANDS = {
    "ip -4 -o addr show scope global": IP_OUTPUT,
    "cat /proc/uptime": "123.45 400.00\r\n",
    "printenv ipaddr serverip ethaddr": (
        "ipaddr=192.168.3.50\r\nserverip=192.168.3.1\r\n"
    ),
    "cat /etc/os-release": 'NAME="Poky"\r\nID=poky\r\n',
}


class FakeDUT(threading.Thread):
    """Scripted fake board on the master side of a pty.

    Modes: shell, login (getty), uboot, booting (prints noise), silent.
    """

    def __init__(
        self,
        master_fd: int,
        mode: str,
        *,
        password: str | None = None,
        respond_to_login: bool = True,
        kernel_noise: bool = False,
        swallow_end_marker: bool = False,
        wrapped_echo: bool = False,
    ) -> None:
        super().__init__(daemon=True)
        self.master = master_fd
        self.mode = mode
        self.password = password
        self.respond_to_login = respond_to_login
        self.kernel_noise = kernel_noise
        self.swallow_end_marker = swallow_end_marker
        self.wrapped_echo = wrapped_echo
        self.received = b""
        self.awaiting_password = False
        self.logged_in = mode == "shell"
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def _send(self, text: str) -> None:
        os.write(self.master, text.encode())

    def run(self) -> None:
        os.set_blocking(self.master, False)
        buffer = b""
        noise_at = time.monotonic()
        while not self._stop.is_set():
            if self.mode == "booting" and time.monotonic() >= noise_at:
                self._send("[    5.1] systemd[1]: starting units...\r\n")
                noise_at = time.monotonic() + 0.1
            try:
                data = os.read(self.master, 4096)
            except BlockingIOError:
                time.sleep(0.01)
                continue
            if not data:
                break
            self.received += data
            buffer += data
            while b"\r" in buffer or b"\n" in buffer:
                line, buffer = re.split(rb"[\r\n]", buffer, maxsplit=1)
                self._on_line(line.decode(errors="replace"))

    def _on_line(self, line: str) -> None:
        if self.mode == "silent":
            return
        if self.mode == "booting":
            return
        if self.mode == "uboot":
            self._run_commands(line, uboot=True)
            self._send("=> ")
            return
        if self.awaiting_password:
            self.awaiting_password = False
            if self.password is not None and line == self.password:
                self.logged_in = True
                self._send("Welcome\r\nroot@imx93:~# ")
            else:
                self._send("Login incorrect\r\nimx93 login: ")
            return
        if not self.logged_in:
            if not self.respond_to_login:
                return
            if line == "":
                self._send("imx93 login: ")
            elif self.password is not None:
                self.awaiting_password = True
                self._send("Password: ")
            else:
                self.logged_in = True
                self._send("root@imx93:~# ")
            return
        # Logged-in shell.
        if line == "":
            self._send("root@imx93:~# ")
            return
        cut = line.find("__RC_", line.find("__RC_") + 1)
        if self.wrapped_echo and cut > 0:
            # Terminal echo wrapped so that the end marker of the echoed
            # command line starts a line of its own, before any output.
            self._send(line[:cut] + "\r\n" + line[cut:] + "\r\n")
        else:
            self._send(line + "\r\n")  # terminal echo
        self._run_commands(line)
        self._send("root@imx93:~# ")

    def _run_commands(self, line: str, *, uboot: bool = False) -> None:
        for part in line.split(";"):
            part = part.strip()
            if not part:
                continue
            if part.startswith("echo "):
                payload = part[5:]
                if payload.endswith(" $?"):
                    payload = payload[:-3] + " 0"
                if self.swallow_end_marker and "_E__" in payload:
                    continue
                if self.kernel_noise:
                    self._send("[   12.345678] eth0: link is up\r\n")
                self._send(payload + "\r\n")
            elif part in COMMANDS:
                self._send(COMMANDS[part])


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture(autouse=True)
def fast_timings(monkeypatch):
    monkeypatch.setattr(capture_mod, "RETRY_INITIAL_S", 0.05)
    monkeypatch.setattr(session_mod, "PROBE_WINDOW_S", 0.5)
    monkeypatch.setattr(session_mod, "ACTION_LOCK_TIMEOUT_S", 0.3)


@pytest.fixture()
def dut_factory(tmp_path):
    created = []

    def _make(mode: str, *, config_kwargs=None, **dut_kwargs):
        master, slave = os.openpty()
        device = os.ttyname(slave)
        dut = FakeDUT(master, mode, **dut_kwargs)
        dut.start()
        config = ConsoleConfig(device=device, **(config_kwargs or {}))
        console = BoardConsole("lab", config, data_dir=tmp_path)
        console.start()
        assert _wait_for(lambda: console.connected)
        # Drop anything echoed before pyserial switched the pty to raw
        # mode, so `received` only holds bytes the session really sent.
        time.sleep(0.2)
        dut.received = b""
        created.append((dut, console, master, slave))
        return dut, console

    yield _make
    for dut, console, master, slave in created:
        dut.stop()
        console.stop()
        for fd in (master, slave):
            try:
                os.close(fd)
            except OSError:
                pass


LOGIN = ConsoleLoginConfig(user="root", shell_prompt=r"[#$] $")

IP_ACTION = ConsoleActionConfig(
    name="ip_address",
    command="ip -4 -o addr show scope global",
    parse="ipv4",
    timeout_s=5.0,
)
UBOOT_ACTION = ConsoleActionConfig(
    name="uboot_net",
    command="printenv ipaddr serverip ethaddr",
    context="uboot",
    parse="kv",
    timeout_s=5.0,
)


# ---------------------------------------------------------------------------
# State probe
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "config_kwargs", "expected"),
    [
        ("shell", {"login": LOGIN}, "linux_shell"),
        ("login", {}, "linux_login"),
        ("uboot", {}, "uboot"),
        ("booting", {}, "booting"),
        ("silent", {}, "silent"),
    ],
)
def test_probe_states(dut_factory, mode, config_kwargs, expected):
    dut, console = dut_factory(mode, config_kwargs=config_kwargs)
    assert ConsoleSession(console).probe_state() == expected
    # The probe writes exactly one carriage return and nothing else.
    assert dut.received == b"\r"


def test_probe_disconnected(tmp_path):
    console = BoardConsole(
        "lab",
        ConsoleConfig(device="/dev/pts/999"),
        data_dir=tmp_path,
        open_port=lambda c: (_ for _ in ()).throw(OSError("gone")),
    )
    console.start()
    try:
        assert ConsoleSession(console).probe_state() == "disconnected"
    finally:
        console.stop()


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def _password_file(tmp_path, secret="hunter2", mode=0o600):
    path = tmp_path / "console.secret"
    path.write_text(secret + "\n")
    path.chmod(mode)
    return str(path)


def test_action_logs_in_without_password(dut_factory):
    dut, console = dut_factory(
        "login", config_kwargs={"login": LOGIN}
    )
    result = ConsoleSession(console).run_action(IP_ACTION)
    assert result["state_before"] == "linux_login"
    assert result["logged_in"] is True
    assert result["exit_code"] == 0
    assert result["parsed"] == [
        {"interface": "eth0", "address": "192.168.3.141", "prefix": 24},
        {"interface": "wlan0", "address": "10.0.0.7", "prefix": 16},
    ]


def test_action_logs_in_with_password(dut_factory, tmp_path):
    login = ConsoleLoginConfig(
        user="root",
        password_file=_password_file(tmp_path),
        shell_prompt=r"[#$] $",
    )
    dut, console = dut_factory(
        "login", config_kwargs={"login": login}, password="hunter2"
    )
    result = ConsoleSession(console).run_action(IP_ACTION)
    assert result["logged_in"] is True
    assert result["parsed"][0]["address"] == "192.168.3.141"


def test_wrong_password_is_login_failed(dut_factory, tmp_path):
    login = ConsoleLoginConfig(
        user="root",
        password_file=_password_file(tmp_path, secret="wrong"),
        shell_prompt=r"[#$] $",
        timeout_s=2.0,
    )
    dut, console = dut_factory(
        "login", config_kwargs={"login": login}, password="hunter2"
    )
    with pytest.raises(ConsoleSessionError) as excinfo:
        ConsoleSession(console).run_action(IP_ACTION)
    assert excinfo.value.code == "login_failed"


def test_login_timeout(dut_factory):
    login = ConsoleLoginConfig(
        user="root", shell_prompt=r"NEVER-SEEN# $", timeout_s=0.5
    )
    dut, console = dut_factory("login", config_kwargs={"login": login})
    with pytest.raises(ConsoleSessionError) as excinfo:
        ConsoleSession(console).run_action(IP_ACTION)
    assert excinfo.value.code == "login_failed"


def test_password_never_leaks(dut_factory, tmp_path):
    login = ConsoleLoginConfig(
        user="root",
        password_file=_password_file(tmp_path),
        shell_prompt=r"[#$] $",
    )
    dut, console = dut_factory(
        "login", config_kwargs={"login": login}, password="hunter2"
    )
    result = ConsoleSession(console).run_action(IP_ACTION)
    assert "hunter2" not in json.dumps(result)
    # Give the capture thread a moment to store the echoed lines.
    _wait_for(lambda: "hunter2" not in "x")
    time.sleep(0.3)
    console.stop()
    for log in (tmp_path / "lab" / "boots").glob("*.log"):
        assert "hunter2" not in log.read_text()


def test_password_file_mode_checked(tmp_path):
    login = ConsoleLoginConfig(
        user="root",
        password_file=_password_file(tmp_path, mode=0o644),
    )
    config = ConsoleConfig(device="/dev/pts/9", login=login)
    with pytest.raises(ConsoleSessionError) as excinfo:
        read_password(config)
    assert "readable by others" in str(excinfo.value)


def test_password_file_missing(tmp_path):
    login = ConsoleLoginConfig(
        user="root", password_file=str(tmp_path / "missing")
    )
    config = ConsoleConfig(device="/dev/pts/9", login=login)
    with pytest.raises(ConsoleSessionError):
        read_password(config)


def test_password_env(monkeypatch):
    login = ConsoleLoginConfig(user="root", password_env="LAB_PW")
    config = ConsoleConfig(device="/dev/pts/9", login=login)
    monkeypatch.setenv("LAB_PW", "fromenv")
    assert read_password(config) == "fromenv"


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def test_linux_action_at_shell(dut_factory):
    dut, console = dut_factory("shell", config_kwargs={"login": LOGIN})
    result = ConsoleSession(console).run_action(IP_ACTION)
    assert result["board"] == "lab"
    assert result["action"] == "ip_address"
    assert result["state_before"] == "linux_shell"
    assert result["logged_in"] is False
    assert result["exit_code"] == 0
    assert result["parsed"][0] == {
        "interface": "eth0", "address": "192.168.3.141", "prefix": 24,
    }
    assert result["duration_s"] >= 0


def test_uboot_action(dut_factory):
    dut, console = dut_factory("uboot")
    result = ConsoleSession(console).run_action(UBOOT_ACTION)
    assert result["state_before"] == "uboot"
    assert result["exit_code"] is None  # no $? in U-Boot
    assert result["parsed"]["ipaddr"] == "192.168.3.50"


def test_kernel_noise_dropped_unless_lines(dut_factory):
    dut, console = dut_factory(
        "shell", config_kwargs={"login": LOGIN}, kernel_noise=True
    )
    result = ConsoleSession(console).run_action(IP_ACTION)
    assert all(
        "link is up" not in line for line in result["untrusted_output"]
    )

    lines_action = ConsoleActionConfig(
        name="uptime", command="cat /proc/uptime", parse="lines",
        timeout_s=5.0,
    )
    result = ConsoleSession(console).run_action(lines_action)
    assert any("link is up" in line for line in result["untrusted_output"])


def test_wrapped_echo_does_not_end_capture_early(dut_factory):
    dut, console = dut_factory(
        "shell", config_kwargs={"login": LOGIN}, wrapped_echo=True
    )
    result = ConsoleSession(console).run_action(IP_ACTION)
    assert result["exit_code"] == 0
    assert result["parsed"], result


def test_scan_markers_ignores_end_before_begin():
    begin = "__RC_ab_B__"
    end_re = re.compile(r"^__RC_ab_E__ (-?[0-9]+)$")
    lines = [
        "root@imx93:~# echo __RC_ab_B__; uname -a;",
        "__RC_ab_E__ $?",
        "__RC_ab_B__",
        "Linux imx93 6.18.20",
        "__RC_ab_E__ 0",
    ]
    output, ended, code = session_mod._scan_markers(lines, begin, end_re, "lines")
    assert (output, ended, code) == (["Linux imx93 6.18.20"], True, 0)


def test_end_marker_timeout(dut_factory):
    dut, console = dut_factory(
        "shell", config_kwargs={"login": LOGIN}, swallow_end_marker=True
    )
    action = ConsoleActionConfig(
        name="ip_address",
        command="ip -4 -o addr show scope global",
        parse="ipv4",
        timeout_s=1.0,
    )
    with pytest.raises(ConsoleSessionError) as excinfo:
        ConsoleSession(console).run_action(action)
    assert excinfo.value.code == "timeout"
    assert "untrusted_output" in excinfo.value.details


@pytest.mark.parametrize(
    ("mode", "action", "fragment"),
    [
        ("uboot", IP_ACTION, "U-Boot prompt"),
        ("shell", UBOOT_ACTION, "in Linux"),
        ("booting", IP_ACTION, "wait_for_console"),
        ("silent", IP_ACTION, "cannot run"),
    ],
)
def test_context_refusals(dut_factory, mode, action, fragment):
    config_kwargs = {"login": LOGIN} if mode == "shell" else {}
    dut, console = dut_factory(mode, config_kwargs=config_kwargs)
    with pytest.raises(ConsoleSessionError) as excinfo:
        ConsoleSession(console).run_action(action)
    assert excinfo.value.code == "wrong_context"
    assert fragment in str(excinfo.value)


def test_action_lock_busy(dut_factory):
    dut, console = dut_factory("shell", config_kwargs={"login": LOGIN})
    assert console.action_lock.acquire()
    try:
        with pytest.raises(ConsoleBusyError):
            ConsoleSession(console).run_action(IP_ACTION)
    finally:
        console.action_lock.release()
    # And once the lock is free, the action goes through.
    result = ConsoleSession(console).run_action(IP_ACTION)
    assert result["exit_code"] == 0


def test_action_writes_note_into_boot_log(dut_factory, tmp_path):
    dut, console = dut_factory("shell", config_kwargs={"login": LOGIN})
    ConsoleSession(console).run_action(
        IP_ACTION, actor="relay-mcp", audit_id="1235"
    )
    boots = console.store.records()
    assert len(boots) == 1  # an action is not a new boot
    console.stop()
    log = (tmp_path / "lab" / "boots" / "000001.log").read_text()
    assert "=== action ip_address by relay-mcp \u00b7 audit 1235 ===" in log
