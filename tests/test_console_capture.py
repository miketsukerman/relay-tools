"""Tests for console capture: pty reading, boots, mirror, waits, URLs."""

from __future__ import annotations

import os
import socket
import threading
import time

import pytest

pytest.importorskip("serial")

from relay_tools.board_config import ConsoleConfig  # noqa: E402
from relay_tools.console import capture as capture_mod  # noqa: E402
from relay_tools.console.capture import BoardConsole  # noqa: E402


@pytest.fixture(autouse=True)
def fast_timings(monkeypatch):
    monkeypatch.setattr(capture_mod, "PARTIAL_FLUSH_S", 0.3)
    monkeypatch.setattr(capture_mod, "RETRY_INITIAL_S", 0.05)
    monkeypatch.setattr(capture_mod, "RETRY_MAX_S", 0.2)


@pytest.fixture()
def pty_pair():
    master, slave = os.openpty()
    yield master, os.ttyname(slave)
    for fd in (master, slave):
        try:
            os.close(fd)
        except OSError:
            pass


def _console(tmp_path, device, **config_kwargs) -> BoardConsole:
    config = ConsoleConfig(device=device, **config_kwargs)
    return BoardConsole("lab", config, data_dir=tmp_path)


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_capture_writes_timestamped_lines(tmp_path, pty_pair):
    master, device = pty_pair
    console = _console(tmp_path, device)
    console.start()
    try:
        assert _wait_for(lambda: console.connected)
        os.write(master, b"hello\nworld\n")
        assert _wait_for(lambda: console.store.current.lines >= 2)
    finally:
        console.stop()
    log = (tmp_path / "lab" / "boots" / "000001.log").read_text()
    lines = log.splitlines()
    assert lines[-2].split("\t", 1)[1] == "hello"
    assert lines[-1].split("\t", 1)[1] == "world"
    stamp = lines[-1].split("\t", 1)[0]
    assert stamp.endswith("Z") and "T" in stamp


def test_partial_line_flushed_after_timeout(tmp_path, pty_pair):
    master, device = pty_pair
    console = _console(tmp_path, device)
    console.start()
    try:
        assert _wait_for(lambda: console.connected)
        os.write(master, b"=> ")  # no newline: a U-Boot prompt
        assert _wait_for(
            lambda: "=> " in console.recent_lines(5), timeout=3.0
        )
    finally:
        console.stop()


def test_marker_starts_new_boot_with_header_line(tmp_path, pty_pair):
    master, device = pty_pair
    console = _console(tmp_path, device)
    console.start()
    try:
        assert _wait_for(lambda: console.connected)
        record = console.post_marker(
            reason="power-cycle", actor="relay-mcp", audit_id="1234"
        )
        assert record.id == 2
        os.write(master, b"booting\n")
        assert _wait_for(lambda: console.store.current.lines >= 2)
    finally:
        console.stop()
    log = (tmp_path / "lab" / "boots" / "000002.log").read_text()
    assert "=== boot 2 \u00b7 power-cycle by relay-mcp \u00b7 audit 1234 ===" in log
    assert "booting" in log


def test_banner_without_marker_starts_detected_boot(tmp_path, pty_pair):
    master, device = pty_pair
    console = _console(tmp_path, device, boot_banner="U-Boot SPL")
    console.start()
    try:
        assert _wait_for(lambda: console.connected)
        # Pretend the last boot started long ago, so suppression is over.
        console._last_boot_start = -1000.0
        os.write(master, b"U-Boot SPL 2025.04\n")
        assert _wait_for(
            lambda: console.store.current.trigger == "detected"
        )
        assert console.store.current.id == 2
    finally:
        console.stop()


def test_banner_shortly_after_marker_stays_in_boot(tmp_path, pty_pair):
    master, device = pty_pair
    console = _console(tmp_path, device, boot_banner="U-Boot SPL")
    console.start()
    try:
        assert _wait_for(lambda: console.connected)
        record = console.post_marker(reason="reset", actor="relay-board")
        os.write(master, b"U-Boot SPL 2025.04\n")
        assert _wait_for(lambda: console.store.current.lines >= 2)
        assert console.store.current.id == record.id
        assert console.store.current.trigger == "marker"
    finally:
        console.stop()


def test_device_disappears_and_comes_back(tmp_path):
    """The capture thread survives open failures and reconnects."""

    class FakePort:
        def __init__(self, text: bytes) -> None:
            self._chunks = [text]

        def read(self, size: int) -> bytes:
            if self._chunks:
                return self._chunks.pop(0)
            time.sleep(0.02)
            return b""

        def close(self) -> None:
            pass

    attempts = []

    def open_port(console):
        attempts.append(1)
        if len(attempts) < 3:
            raise OSError("device gone")
        return FakePort(b"back again\n")

    console = BoardConsole(
        "lab",
        ConsoleConfig(device="/dev/pts/999"),
        data_dir=tmp_path,
        open_port=open_port,
    )
    console.start()
    try:
        assert _wait_for(lambda: console.connected)
        assert len(attempts) >= 3
        assert _wait_for(lambda: "back again" in console.recent_lines(5))
    finally:
        console.stop()


def test_boot_ids_survive_service_restart(tmp_path, pty_pair):
    master, device = pty_pair
    console = _console(tmp_path, device)
    console.start()
    assert _wait_for(lambda: console.connected)
    console.stop()

    console2 = _console(tmp_path, device)
    console2.start()
    try:
        assert console2.store.current.id == 2
        assert console2.store.current.trigger == "service_start"
    finally:
        console2.stop()


def test_wait_for_line_match_and_timeout(tmp_path, pty_pair):
    master, device = pty_pair
    console = _console(tmp_path, device)
    console.start()
    try:
        assert _wait_for(lambda: console.connected)

        def _feed():
            time.sleep(0.2)
            os.write(master, b"noise\nimx93 login:\n")

        threading.Thread(target=_feed).start()
        result = console.wait_for_line("login:", timeout_s=5)
        assert result["matched"] is True
        assert "login:" in result["match"]
        assert "noise" in result["untrusted_lines"]

        result = console.wait_for_line("never-matches", timeout_s=0.3)
        assert result["matched"] is False
        assert result["untrusted_lines"]  # last lines on timeout
    finally:
        console.stop()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_mirror_streams_and_discards_input(tmp_path, pty_pair):
    master, device = pty_pair
    port = _free_port()
    console = _console(tmp_path, device, mirror_port=port)
    console.start()
    try:
        assert _wait_for(lambda: console.connected)
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        client.settimeout(5)
        time.sleep(0.1)
        # Whatever a client sends is discarded, never written to the UART.
        client.sendall(b"reboot\n")
        os.write(master, b"mirrored line\n")
        received = b""
        while b"mirrored line" not in received:
            received += client.recv(4096)
        client.close()
        # Nothing was forwarded to the pty.
        os.set_blocking(master, False)
        with pytest.raises(BlockingIOError):
            os.read(master, 4096)
    finally:
        console.stop()


def test_mirror_client_limit(tmp_path, pty_pair):
    master, device = pty_pair
    port = _free_port()
    console = _console(tmp_path, device, mirror_port=port)
    console.start()
    clients = []
    try:
        assert _wait_for(lambda: console.connected)
        for _ in range(4):
            clients.append(
                socket.create_connection(("127.0.0.1", port), timeout=5)
            )
        time.sleep(0.2)
        fifth = socket.create_connection(("127.0.0.1", port), timeout=5)
        fifth.settimeout(2)
        # The fifth client is rejected: the server closes it immediately.
        assert fifth.recv(1) == b""
        fifth.close()
    finally:
        for client in clients:
            client.close()
        console.stop()


def test_socket_url_device(tmp_path, pty_pair):
    """A socket:// URL pointing at a TCP server relaying a pty works."""

    master, device = pty_pair
    port = _free_port()
    stop = threading.Event()

    def _relay_server():
        with socket.socket() as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", port))
            server.listen(1)
            server.settimeout(5)
            conn, _ = server.accept()
            conn.settimeout(0.1)
            with conn:
                while not stop.is_set():
                    try:
                        data = conn.recv(4096)
                        if not data:
                            break
                        os.write(master, data)
                    except socket.timeout:
                        pass
                    os.set_blocking(master, False)
                    try:
                        out = os.read(master, 4096)
                        if out:
                            conn.sendall(out)
                    except BlockingIOError:
                        pass

    thread = threading.Thread(target=_relay_server, daemon=True)
    thread.start()
    time.sleep(0.1)

    console = _console(tmp_path, f"socket://127.0.0.1:{port}")
    console.start()
    try:
        assert _wait_for(lambda: console.connected)
        os.write(master, b"over tcp\n")
        assert _wait_for(lambda: "over tcp" in console.recent_lines(5))
        # Writes travel back through the TCP relay to the pty master.
        console.write(b"ping\n")
        received = b""
        os.set_blocking(master, True)
        deadline = time.monotonic() + 5
        while b"ping" not in received and time.monotonic() < deadline:
            received += os.read(master, 4096)
        assert b"ping" in received
    finally:
        stop.set()
        console.stop()
