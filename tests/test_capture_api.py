"""Tests for the capture FastAPI router with stub hub and recorder."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay_tools.capture.api import create_app, router
from relay_tools.capture.hub import (
    CaptureConfig,
    FrameTimeoutError,
    RecorderBusyError,
)

FRAME = b"\xff\xd8" + b"J" * 16 + b"\xff\xd9"


class StubHub:
    def __init__(self, frame: "bytes | None" = FRAME, ts: float = 1234.5):
        self.frame = frame
        self.latest = frame
        self.latest_ts = ts if frame else 0.0
        self.device = "/dev/video0"
        self.cfg = CaptureConfig()

    async def next_frame(self, timeout: float):
        if self.frame is None:
            raise FrameTimeoutError(f"no frame captured within {timeout:.1f}s")
        return self.latest_ts, self.frame

    @property
    def measured_fps(self) -> float:
        return 30.0 if self.frame else 0.0


class StubRecorder:
    def __init__(self):
        self.active = False
        self.path: "Path | None" = None

    async def start(self, name: "str | None") -> Path:
        if self.active:
            raise RecorderBusyError("a recording is already active")
        if name is not None and ("/" in name or name.startswith(".")):
            raise ValueError(f"invalid recording name {name!r}")
        self.active = True
        self.path = Path("/var/lib/relay-capture") / f"{name or 'ts'}.mkv"
        return self.path

    async def stop(self) -> "Path | None":
        if not self.active:
            return None
        self.active = False
        return self.path


@pytest.fixture()
def hub() -> StubHub:
    return StubHub()


@pytest.fixture()
def recorder() -> StubRecorder:
    return StubRecorder()


@pytest.fixture()
def client(hub: StubHub, recorder: StubRecorder) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.hub = hub
    app.state.recorder = recorder
    return TestClient(app)


def test_snapshot_returns_jpeg(client: TestClient):
    response = client.get("/capture/snapshot")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-frame-time"] == "1234.500"
    assert response.content == FRAME


def test_snapshot_503_on_timeout(client: TestClient, hub: StubHub):
    hub.frame = None
    response = client.get("/capture/snapshot", params={"timeout": 0.5})
    assert response.status_code == 503
    assert "no frame" in response.json()["detail"]


def test_snapshot_rejects_bad_timeout(client: TestClient):
    assert client.get("/capture/snapshot", params={"timeout": 0}).status_code == 422
    assert client.get("/capture/snapshot", params={"timeout": 99}).status_code == 422


def test_record_start_and_stop(client: TestClient, recorder: StubRecorder):
    response = client.post("/capture/record/start", params={"name": "boot"})
    assert response.status_code == 200
    assert response.json() == {"file": "/var/lib/relay-capture/boot.mkv"}
    assert recorder.active

    response = client.post("/capture/record/stop")
    assert response.status_code == 200
    assert response.json() == {"file": "/var/lib/relay-capture/boot.mkv"}
    assert not recorder.active


def test_record_start_conflict(client: TestClient):
    assert client.post("/capture/record/start").status_code == 200
    response = client.post("/capture/record/start")
    assert response.status_code == 409


def test_record_start_invalid_name(client: TestClient):
    response = client.post(
        "/capture/record/start", params={"name": "../evil"}
    )
    assert response.status_code == 422


def test_record_stop_without_recording(client: TestClient):
    response = client.post("/capture/record/stop")
    assert response.status_code == 200
    assert response.json() == {"file": None}


def test_status(client: TestClient, recorder: StubRecorder):
    response = client.get("/capture/status")
    assert response.status_code == 200
    data = response.json()
    assert data["fps"] == 30.0
    assert data["last_frame_age_s"] is not None
    assert data["recording"] is None
    assert data["device"] == "/dev/video0"

    client.post("/capture/record/start", params={"name": "x"})
    data = client.get("/capture/status").json()
    assert data["recording"] == "/var/lib/relay-capture/x.mkv"


def test_status_without_frames(client: TestClient, hub: StubHub):
    hub.frame = None
    hub.latest_ts = 0.0
    data = client.get("/capture/status").json()
    assert data["fps"] == 0.0
    assert data["last_frame_age_s"] is None


def test_create_app_lifespan(tmp_path: Path):
    """create_app starts and shuts down cleanly even without a device."""
    cfg = CaptureConfig(
        device=str(tmp_path / "missing*"),
        outdir=tmp_path,
        ffmpeg="/bin/false",
    )
    app = create_app(cfg)
    with TestClient(app) as client:
        data = client.get("/capture/status").json()
        assert data["fps"] == 0.0
        assert data["recording"] is None
