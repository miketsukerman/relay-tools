"""Tests for FrameHub and Recorder with a fake ffmpeg executable."""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import pytest

from relay_tools.capture import hub as hub_module
from relay_tools.capture.hub import (
    CaptureConfig,
    DeviceNotFoundError,
    FrameHub,
    FrameTimeoutError,
    Recorder,
    RecorderBusyError,
    resolve_device,
)

FRAME_A = b"\xff\xd8" + b"A" * 32 + b"\xff\xd9"
FRAME_B = b"\xff\xd8" + b"B" * 32 + b"\xff\xd9"


def _fake_ffmpeg(tmp_path: Path, body: str) -> str:
    """Create a fake ffmpeg shell script in tmp_path."""
    script = tmp_path / "fake-ffmpeg"
    script.write_text("#!/bin/sh\n" + body + "\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


def _config(tmp_path: Path, ffmpeg: str, **kwargs) -> CaptureConfig:
    device = tmp_path / "video0"
    device.touch()
    defaults = dict(
        device=str(device),
        timeout=0.5,
        outdir=tmp_path / "recordings",
        ffmpeg=ffmpeg,
    )
    defaults.update(kwargs)
    return CaptureConfig(**defaults)


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
    monkeypatch.setattr(hub_module, "RETRY_DELAY_S", 0.05)


# ---------------------------------------------------------------------------
# resolve_device
# ---------------------------------------------------------------------------


def test_resolve_device_first_sorted_match(tmp_path: Path):
    (tmp_path / "video1").touch()
    (tmp_path / "video0").touch()
    assert resolve_device(str(tmp_path / "video*")) == str(tmp_path / "video0")


def test_resolve_device_no_match(tmp_path: Path):
    with pytest.raises(DeviceNotFoundError):
        resolve_device(str(tmp_path / "nope*"))


# ---------------------------------------------------------------------------
# FrameHub
# ---------------------------------------------------------------------------


async def test_subscribers_receive_frames(tmp_path: Path):
    frames_file = tmp_path / "frames.bin"
    frames_file.write_bytes(FRAME_A + FRAME_B)
    ffmpeg = _fake_ffmpeg(
        tmp_path, f"cat {frames_file}\nsleep 30"
    )
    hub = FrameHub(_config(tmp_path, ffmpeg, timeout=5.0))
    queue = hub.subscribe()
    task = asyncio.create_task(hub.run())
    try:
        ts_a, frame_a = await asyncio.wait_for(queue.get(), 5)
        ts_b, frame_b = await asyncio.wait_for(queue.get(), 5)
        assert frame_a == FRAME_A
        assert frame_b == FRAME_B
        assert ts_b >= ts_a > 0
        assert hub.latest == FRAME_B
        assert hub.latest_ts == ts_b
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_next_frame_returns_next_frame(tmp_path: Path):
    frames_file = tmp_path / "frames.bin"
    frames_file.write_bytes(FRAME_A)
    # Emits one frame every 0.2s, forever.
    ffmpeg = _fake_ffmpeg(
        tmp_path,
        f"while true; do cat {frames_file}; sleep 0.2; done",
    )
    hub = FrameHub(_config(tmp_path, ffmpeg, timeout=5.0))
    task = asyncio.create_task(hub.run())
    try:
        ts1, frame = await hub.next_frame(timeout=5)
        assert frame == FRAME_A
        ts2, _ = await hub.next_frame(timeout=5)
        assert ts2 > ts1
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_next_frame_times_out_without_frames(tmp_path: Path):
    ffmpeg = _fake_ffmpeg(tmp_path, "sleep 30")
    hub = FrameHub(_config(tmp_path, ffmpeg, timeout=10.0))
    task = asyncio.create_task(hub.run())
    try:
        with pytest.raises(FrameTimeoutError):
            await hub.next_frame(timeout=0.2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_hub_restarts_after_ffmpeg_exits(tmp_path: Path):
    frames_file = tmp_path / "frames.bin"
    frames_file.write_bytes(FRAME_A)
    # Writes one frame, then exits: every received frame is one ffmpeg run.
    ffmpeg = _fake_ffmpeg(tmp_path, f"cat {frames_file}")
    hub = FrameHub(_config(tmp_path, ffmpeg, timeout=5.0))
    queue = hub.subscribe(maxsize=8)
    task = asyncio.create_task(hub.run())
    try:
        for _ in range(3):
            _, frame = await asyncio.wait_for(queue.get(), 5)
            assert frame == FRAME_A
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_hub_survives_missing_device(tmp_path: Path):
    cfg = CaptureConfig(
        device=str(tmp_path / "missing*"),
        timeout=0.5,
        outdir=tmp_path,
        ffmpeg="/bin/false",
    )
    hub = FrameHub(cfg)
    task = asyncio.create_task(hub.run())
    await asyncio.sleep(0.3)
    assert not task.done()  # keeps retrying instead of crashing
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_slow_subscriber_drops_oldest_frame(tmp_path: Path):
    hub = FrameHub(_config(tmp_path, "ffmpeg"))
    queue = hub.subscribe(maxsize=1)
    hub._publish(FRAME_A)
    hub._publish(FRAME_B)
    _, frame = queue.get_nowait()
    assert frame == FRAME_B
    assert queue.empty()


def test_measured_fps_zero_when_stale(tmp_path: Path):
    hub = FrameHub(_config(tmp_path, "ffmpeg"))
    assert hub.measured_fps == 0.0
    hub.latest_ts = 1.0  # ancient
    hub._frame_times.append(1.0)
    assert hub.measured_fps == 0.0


async def test_measured_fps_counts_recent_frames(tmp_path: Path):
    hub = FrameHub(_config(tmp_path, "ffmpeg"))
    for _ in range(10):
        hub._publish(FRAME_A)
    assert hub.measured_fps == pytest.approx(10 / 2.0)


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["../x", "a/b", ".hidden", "", "a" * 101])
async def test_recorder_rejects_invalid_names(tmp_path: Path, name: str):
    hub = FrameHub(_config(tmp_path, "ffmpeg"))
    recorder = Recorder(hub, hub.cfg)
    with pytest.raises(ValueError):
        await recorder.start(name)
    assert not recorder.active


async def test_recorder_rejects_second_start(tmp_path: Path):
    ffmpeg = _fake_ffmpeg(tmp_path, "cat > /dev/null")
    hub = FrameHub(_config(tmp_path, ffmpeg))
    recorder = Recorder(hub, hub.cfg)
    path = await recorder.start("boot-test")
    try:
        assert recorder.active
        assert path == hub.cfg.outdir / "boot-test.mkv"
        with pytest.raises(RecorderBusyError):
            await recorder.start("other")
    finally:
        assert await recorder.stop() == path
    assert not recorder.active
    assert await recorder.stop() is None


async def test_recorder_default_name_is_timestamp(tmp_path: Path):
    ffmpeg = _fake_ffmpeg(tmp_path, "cat > /dev/null")
    hub = FrameHub(_config(tmp_path, ffmpeg))
    recorder = Recorder(hub, hub.cfg)
    path = await recorder.start(None)
    try:
        assert path.suffix == ".mkv"
        assert len(path.stem) == 15  # YYYYmmdd_HHMMSS
        assert os.path.isdir(hub.cfg.outdir)
    finally:
        await recorder.stop()


async def test_recorder_writes_frames_to_ffmpeg_stdin(tmp_path: Path):
    sink = tmp_path / "sink.bin"
    ffmpeg = _fake_ffmpeg(tmp_path, f'cat > "{sink}"')
    hub = FrameHub(_config(tmp_path, ffmpeg))
    recorder = Recorder(hub, hub.cfg)
    await recorder.start("rec")
    hub._publish(FRAME_A)
    hub._publish(FRAME_B)
    await asyncio.sleep(0.2)
    await recorder.stop()
    assert sink.read_bytes() == FRAME_A + FRAME_B
