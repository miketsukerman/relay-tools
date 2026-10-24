"""Serial capture: one thread per board, plus taps and the TCP mirror."""

from __future__ import annotations

import logging
import queue
import re
import socket
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from relay_tools.board_config import ConsoleConfig

from .sanitize import Redactor, sanitize_chunk, sanitize_line
from .store import BootRecord, BootStore, LogLine

monotonic = time.monotonic

logger = logging.getLogger(__name__)

PARTIAL_FLUSH_S = 2.0
BANNER_SUPPRESS_S = 10.0
RETRY_INITIAL_S = 2.0
RETRY_MAX_S = 30.0
RECENT_LINES = 100
MAX_MIRROR_CLIENTS = 4


class ConsoleUnavailableError(RuntimeError):
    """Raised when the serial device is disconnected."""


class Tap:
    """Receives sanitized text chunks from the capture thread."""

    def __init__(self) -> None:
        self.chunks: "queue.Queue[str]" = queue.Queue()

    def put(self, text: str) -> None:
        self.chunks.put(text)


class _MirrorServer:
    """Read-only TCP mirror of the sanitized live stream."""

    def __init__(self, port: int) -> None:
        self.port = port
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._server = socket.create_server(("", port), reuse_port=False)
        self._server.settimeout(0.5)
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._lock:
                if len(self._clients) >= MAX_MIRROR_CLIENTS:
                    client.close()
                    continue
                self._clients.append(client)
            # Drain and discard anything the client sends.
            threading.Thread(
                target=self._drain, args=(client,), daemon=True
            ).start()

    def _drain(self, client: socket.socket) -> None:
        try:
            while client.recv(4096):
                pass
        except OSError:
            pass
        self._remove(client)

    def _remove(self, client: socket.socket) -> None:
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)
        try:
            client.close()
        except OSError:
            pass

    def broadcast(self, text: str) -> None:
        data = text.encode("utf-8", errors="replace")
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.sendall(data)
            except OSError:
                self._remove(client)

    def close(self) -> None:
        self._stop.set()
        try:
            self._server.close()
        except OSError:
            pass
        with self._lock:
            clients = list(self._clients)
            self._clients.clear()
        for client in clients:
            try:
                client.close()
            except OSError:
                pass


class BoardConsole:
    """Owns one board's UART: capture thread, boot store, taps, mirror."""

    def __init__(
        self,
        board: str,
        config: ConsoleConfig,
        *,
        data_dir: str | Path,
        open_port: Callable[["BoardConsole"], object] | None = None,
    ) -> None:
        self.board = board
        self.config = config
        self.redactor = Redactor(config.redact)
        self.store = BootStore(
            Path(data_dir) / board,
            retention=config.retention,
            result_fn=self._classify_boot,
        )
        self.connected = False
        self._open_port = open_port or _default_open_port
        self._port: object | None = None
        self._port_lock = threading.Lock()
        self.action_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._taps: list[Tap] = []
        self._taps_lock = threading.Lock()
        self._recent: "deque[str]" = deque(maxlen=RECENT_LINES)
        self._line_event = threading.Condition()
        self._line_counter = 0
        self._last_boot_start = -BANNER_SUPPRESS_S
        self._banner_re = (
            re.compile(config.boot_banner) if config.boot_banner else None
        )
        self._mirror = (
            _MirrorServer(config.mirror_port) if config.mirror_port else None
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self.store.new_boot(trigger="service_start")
        self._last_boot_start = monotonic()
        self._thread = threading.Thread(
            target=self._run, name=f"console-{self.board}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        with self._port_lock:
            if self._port is not None:
                try:
                    self._port.close()  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass
                self._port = None
        if self._mirror is not None:
            self._mirror.close()
        self.store.close()

    def _classify_boot(self, lines: list[LogLine], record: BootRecord) -> str:
        from .report import build_report

        return build_report(
            lines, record, config=self.config, board=self.board
        )["result"]

    # ------------------------------------------------------------------
    # Capture loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        retry_delay = RETRY_INITIAL_S
        while not self._stop.is_set():
            try:
                port = self._open_port(self)
            except Exception as exc:  # noqa: BLE001 - keep retrying
                if self.connected:
                    logger.warning(
                        "console %s: device %s unavailable: %s",
                        self.board,
                        self.config.device,
                        exc,
                    )
                self.connected = False
                if self._stop.wait(retry_delay):
                    return
                retry_delay = min(retry_delay * 2, RETRY_MAX_S)
                continue
            retry_delay = RETRY_INITIAL_S
            with self._port_lock:
                self._port = port
            self.connected = True
            logger.info(
                "console %s: capturing %s", self.board, self.config.device
            )
            try:
                self._read_loop(port)
            except Exception as exc:  # noqa: BLE001 - device vanished
                logger.warning(
                    "console %s: read failed: %s", self.board, exc
                )
            finally:
                self.connected = False
                with self._port_lock:
                    self._port = None
                try:
                    port.close()  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass

    def _read_loop(self, port: object) -> None:
        buffer = b""
        last_data = monotonic()
        while not self._stop.is_set():
            data = port.read(4096)  # type: ignore[attr-defined]
            if data:
                tap_text = sanitize_chunk(data, self.redactor)
                if tap_text:
                    self._notify_taps(tap_text)
                buffer += data
                last_data = monotonic()
                while b"\n" in buffer:
                    raw_line, buffer = buffer.split(b"\n", 1)
                    self._handle_line(sanitize_line(raw_line, self.redactor))
            elif buffer and monotonic() - last_data >= PARTIAL_FLUSH_S:
                self._handle_line(sanitize_line(buffer, self.redactor))
                buffer = b""

    def _handle_line(self, text: str) -> None:
        if (
            self._banner_re is not None
            and self._banner_re.search(text)
            and monotonic() - self._last_boot_start > BANNER_SUPPRESS_S
        ):
            self._last_boot_start = monotonic()
            self.store.new_boot(trigger="detected")
        self.store.append_line(text)
        if self._mirror is not None:
            self._mirror.broadcast(text + "\n")
        with self._line_event:
            self._recent.append(text)
            self._line_counter += 1
            self._line_event.notify_all()

    # ------------------------------------------------------------------
    # Markers and notes
    # ------------------------------------------------------------------

    def post_marker(
        self,
        *,
        reason: str,
        actor: str,
        audit_id: str | None = None,
    ) -> BootRecord:
        """Close the current boot and start a new, marker-triggered one."""

        self._last_boot_start = monotonic()
        record = self.store.new_boot(
            trigger="marker", reason=reason, actor=actor, audit_id=audit_id
        )
        marker = f"=== boot {record.id} · {reason} by {actor}"
        if audit_id:
            marker += f" · audit {audit_id}"
        marker += " ==="
        self.store.append_line(marker)
        return record

    def note(self, text: str) -> None:
        """Write an annotation line into the current boot log."""

        self.store.append_line(text)

    # ------------------------------------------------------------------
    # Taps, waits and writes (used by sessions, wait and the mirror)
    # ------------------------------------------------------------------

    def open_tap(self) -> Tap:
        tap = Tap()
        with self._taps_lock:
            self._taps.append(tap)
        return tap

    def close_tap(self, tap: Tap) -> None:
        with self._taps_lock:
            if tap in self._taps:
                self._taps.remove(tap)

    def _notify_taps(self, text: str) -> None:
        with self._taps_lock:
            taps = list(self._taps)
        for tap in taps:
            tap.put(text)

    def recent_lines(self, count: int = 10) -> list[str]:
        with self._line_event:
            return list(self._recent)[-count:]

    def write(self, data: bytes) -> None:
        """Write to the UART. Only sessions may call this."""

        with self._port_lock:
            port = self._port
        if port is None or not self.connected:
            raise ConsoleUnavailableError(
                f"console device for board {self.board!r} is disconnected"
            )
        port.write(data)  # type: ignore[attr-defined]
        flush = getattr(port, "flush", None)
        if flush is not None:
            flush()

    def wait_for_line(
        self,
        pattern: str,
        *,
        timeout_s: float,
    ) -> dict[str, object]:
        """Block until a new line matches ``pattern`` or time runs out."""

        compiled = re.compile(pattern)
        deadline = monotonic() + timeout_s
        started = monotonic()
        with self._line_event:
            seen = self._line_counter
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return {
                        "matched": False,
                        "elapsed_s": round(monotonic() - started, 1),
                        "untrusted_lines": list(self._recent)[-50:],
                    }
                if self._line_counter > seen:
                    new_count = self._line_counter - seen
                    recent = list(self._recent)
                    new_lines = recent[-new_count:]
                    for offset, line in enumerate(new_lines):
                        if compiled.search(line):
                            absolute = len(recent) - new_count + offset
                            return {
                                "matched": True,
                                "match": line,
                                "elapsed_s": round(monotonic() - started, 1),
                                "untrusted_lines": recent[
                                    max(0, absolute - 20):absolute
                                ],
                            }
                    seen = self._line_counter
                self._line_event.wait(timeout=min(remaining, 0.5))


def _default_open_port(console: BoardConsole) -> object:
    import serial  # imported lazily: pyserial is an optional extra

    config = console.config
    parity_map = {
        "none": serial.PARITY_NONE,
        "even": serial.PARITY_EVEN,
        "odd": serial.PARITY_ODD,
    }
    stop_map = {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO}
    bytesize_map = {
        5: serial.FIVEBITS,
        6: serial.SIXBITS,
        7: serial.SEVENBITS,
        8: serial.EIGHTBITS,
    }
    return serial.serial_for_url(
        config.device,
        baudrate=config.baud,
        bytesize=bytesize_map[config.data_bits],
        parity=parity_map[config.parity],
        stopbits=stop_map[config.stop_bits],
        timeout=0.2,
    )
