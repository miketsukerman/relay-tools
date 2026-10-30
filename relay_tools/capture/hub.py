"""Capture source, frame fan-out hub and MKV recorder.

One long-lived ffmpeg process stream-copies MJPEG frames from the V4L2
device to stdout; :class:`FrameHub` splits them and fans them out to any
number of consumers through bounded queues. A slow consumer loses its
oldest frame; the capture loop never blocks. A watchdog restarts ffmpeg
whenever the stream stalls, ffmpeg exits or the device disappears.

:class:`Recorder` writes frames to an MKV file through a second ffmpeg
process, again without re-encoding (``-c:v copy``).
"""

from __future__ import annotations

import asyncio
import glob
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Deque, Optional, Set, Tuple

from .mjpeg import MjpegSplitter

logger = logging.getLogger(__name__)

DEFAULT_DEVICE_GLOB = "/dev/v4l/by-id/usb-*-video-index0"
DEFAULT_OUTDIR = Path("/var/lib/relay-capture")
RETRY_DELAY_S = 2.0
FPS_WINDOW_S = 2.0
STALE_FRAME_S = 2.0
READ_CHUNK = 65536
RECORD_NAME_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,99}$")

Frame = Tuple[float, bytes]


class CaptureError(RuntimeError):
    """Base exception for capture failures."""


class DeviceNotFoundError(CaptureError):
    """Raised when the device glob matches nothing."""


class FrameTimeoutError(CaptureError):
    """Raised when no frame arrives within the requested timeout."""


class RecorderBusyError(CaptureError):
    """Raised when a recording is already active."""


@dataclass
class CaptureConfig:
    """Runtime configuration for the capture daemon."""

    device: str = DEFAULT_DEVICE_GLOB
    size: str = "1920x1080"
    fps: int = 30
    timeout: float = 5.0
    outdir: Path = DEFAULT_OUTDIR
    ffmpeg: str = "ffmpeg"


def resolve_device(pattern: str) -> str:
    """Resolve the device glob to a concrete path (first sorted match)."""
    matches = sorted(glob.glob(pattern))
    if not matches:
        raise DeviceNotFoundError(
            f"no capture device matches {pattern!r}; is the HDMI-to-USB "
            "dongle plugged in? (check: ls /dev/v4l/by-id/)"
        )
    return matches[0]


class FrameHub:
    """Reads MJPEG frames from one ffmpeg process and fans them out."""

    def __init__(self, cfg: CaptureConfig) -> None:
        self.cfg = cfg
        self.latest: Optional[bytes] = None
        self.latest_ts: float = 0.0
        self.device: Optional[str] = None
        self._subscribers: Set[asyncio.Queue] = set()
        self._frame_times: Deque[float] = deque()
        self._next_fut: Optional[asyncio.Future] = None
        self._proc: Optional[asyncio.subprocess.Process] = None

    # ------------------------------------------------------------------
    # Consumers
    # ------------------------------------------------------------------

    def subscribe(self, maxsize: int = 4) -> asyncio.Queue:
        """Register a bounded frame queue of ``(timestamp, jpeg)`` tuples."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    async def next_frame(self, timeout: float) -> Frame:
        """Wait for the *next* captured frame (never the cached one)."""
        if self._next_fut is None or self._next_fut.done():
            self._next_fut = asyncio.get_running_loop().create_future()
        try:
            return await asyncio.wait_for(
                asyncio.shield(self._next_fut), timeout
            )
        except asyncio.TimeoutError:
            raise FrameTimeoutError(
                f"no frame captured within {timeout:.1f}s"
            ) from None

    @property
    def measured_fps(self) -> float:
        """Frames per second over a sliding window; 0 when the feed is stale."""
        now = time.time()
        times = self._frame_times
        while times and times[0] < now - FPS_WINDOW_S:
            times.popleft()
        if not times or now - self.latest_ts > STALE_FRAME_S:
            return 0.0
        return len(times) / FPS_WINDOW_S

    # ------------------------------------------------------------------
    # Capture loop
    # ------------------------------------------------------------------

    def _publish(self, frame: bytes) -> None:
        ts = time.time()
        self.latest = frame
        self.latest_ts = ts
        self._frame_times.append(ts)
        while self._frame_times and self._frame_times[0] < ts - FPS_WINDOW_S:
            self._frame_times.popleft()
        if self._next_fut is not None and not self._next_fut.done():
            self._next_fut.set_result((ts, frame))
        for queue in list(self._subscribers):
            if queue.full():
                # Slow consumer: drop its oldest frame, never block capture.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait((ts, frame))
            except asyncio.QueueFull:  # pragma: no cover - defensive
                pass

    def _ffmpeg_command(self, device: str) -> "list[str]":
        return [
            self.cfg.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "v4l2",
            "-input_format",
            "mjpeg",
            "-video_size",
            self.cfg.size,
            "-framerate",
            str(self.cfg.fps),
            "-i",
            device,
            "-c:v",
            "copy",
            "-f",
            "mjpeg",
            "pipe:1",
        ]

    async def _capture_once(self) -> None:
        device = resolve_device(self.cfg.device)
        self.device = device
        proc = await asyncio.create_subprocess_exec(
            *self._ffmpeg_command(device),
            stdout=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        self._proc = proc
        logger.info("capture: ffmpeg started on %s (pid %d)", device, proc.pid)
        splitter = MjpegSplitter()
        assert proc.stdout is not None
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        proc.stdout.read(READ_CHUNK), self.cfg.timeout
                    )
                except asyncio.TimeoutError:
                    raise CaptureError(
                        f"no data from ffmpeg for {self.cfg.timeout:.1f}s "
                        "(no HDMI signal, or the device stalled)"
                    ) from None
                if not chunk:
                    raise CaptureError(
                        f"ffmpeg exited (rc={await proc.wait()})"
                    )
                for frame in splitter.feed(chunk):
                    self._publish(frame)
        finally:
            self._proc = None
            if proc.returncode is None:
                proc.kill()
            await proc.wait()

    async def run(self) -> None:
        """Capture forever; on any failure log a warning and retry."""
        while True:
            try:
                await self._capture_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "capture: %s; retrying in %.0fs", exc, RETRY_DELAY_S
                )
            await asyncio.sleep(RETRY_DELAY_S)


class Recorder:
    """Records hub frames to an MKV file via ffmpeg stream copy."""

    def __init__(self, hub: FrameHub, cfg: CaptureConfig) -> None:
        self.hub = hub
        self.cfg = cfg
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._queue: Optional[asyncio.Queue] = None
        self._task: Optional[asyncio.Task] = None
        self._path: Optional[Path] = None

    @property
    def active(self) -> bool:
        return self._proc is not None

    @property
    def path(self) -> Optional[Path]:
        return self._path

    async def start(self, name: "str | None" = None) -> Path:
        """Start recording to ``<outdir>/<name>.mkv``; one at a time."""
        if self.active:
            raise RecorderBusyError(
                f"a recording is already active: {self._path}"
            )
        if name is None:
            name = datetime.now().strftime("%Y%m%d_%H%M%S")
        if not RECORD_NAME_RE.match(name):
            raise ValueError(
                f"invalid recording name {name!r}: it must match "
                "[A-Za-z0-9_-][A-Za-z0-9._-]{0,99} "
                "(no path separators, no leading dot)"
            )
        outdir = Path(self.cfg.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        path = outdir / f"{name}.mkv"
        proc = await asyncio.create_subprocess_exec(
            self.cfg.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-use_wallclock_as_timestamps",
            "1",
            "-f",
            "mjpeg",
            "-i",
            "pipe:0",
            "-c:v",
            "copy",
            str(path),
            stdin=asyncio.subprocess.PIPE,
        )
        self._proc = proc
        self._path = path
        self._queue = self.hub.subscribe(maxsize=64)
        self._task = asyncio.create_task(self._pump())
        logger.info("recorder: started %s (pid %d)", path, proc.pid)
        return path

    async def _pump(self) -> None:
        assert self._proc is not None and self._queue is not None
        proc, queue = self._proc, self._queue
        assert proc.stdin is not None
        try:
            while True:
                _, frame = await queue.get()
                proc.stdin.write(frame)
                await proc.stdin.drain()
        except asyncio.CancelledError:
            raise
        except (BrokenPipeError, ConnectionResetError):
            logger.warning("recorder: ffmpeg closed the pipe")
        except Exception:  # pragma: no cover - defensive
            logger.exception("recorder: frame pump failed")

    async def stop(self) -> Optional[Path]:
        """Stop the recording and finalize the file; None when inactive."""
        if not self.active:
            return None
        proc, queue, task, path = (
            self._proc,
            self._queue,
            self._task,
            self._path,
        )
        self._proc = None
        self._queue = None
        self._task = None
        if queue is not None:
            self.hub.unsubscribe(queue)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        assert proc is not None
        if proc.stdin is not None:
            try:
                proc.stdin.close()
                await proc.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
        try:
            # Closing stdin makes ffmpeg flush and finalize the MKV.
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:  # pragma: no cover - defensive
            logger.warning("recorder: ffmpeg did not exit; killing it")
            proc.kill()
            await proc.wait()
        logger.info("recorder: finished %s", path)
        return path
