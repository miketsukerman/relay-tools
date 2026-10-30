"""FastAPI app and reusable router for the capture daemon.

The router carries the ``/capture`` prefix so it can also be mounted into
another FastAPI application later; it only relies on ``app.state.hub``,
``app.state.recorder`` and ``app.state.capture_config``.

Endpoints
---------
GET  /capture/snapshot      – next frame as image/jpeg (503 on timeout)
GET  /capture/stream        – multipart/x-mixed-replace live MJPEG stream
POST /capture/record/start  – start an MKV recording (409 when active)
POST /capture/record/stop   – stop the recording and finalize the file
GET  /capture/status        – fps, last frame age, recording, device
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from .hub import (
    CaptureConfig,
    FrameHub,
    FrameTimeoutError,
    Recorder,
    RecorderBusyError,
)

STREAM_BOUNDARY = b"frame"

router = APIRouter(prefix="/capture", tags=["capture"])


def _hub(request: Request) -> FrameHub:
    return request.app.state.hub


def _recorder(request: Request) -> Recorder:
    return request.app.state.recorder


@router.get("/snapshot", summary="Take a screenshot of the DUT")
async def snapshot(
    request: Request,
    timeout: float = Query(default=3.0, gt=0, le=60),
) -> Response:
    """Return the next captured frame as a JPEG image."""
    try:
        ts, frame = await _hub(request).next_frame(timeout)
    except FrameTimeoutError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content=frame,
        media_type="image/jpeg",
        headers={"X-Frame-Time": f"{ts:.3f}"},
    )


@router.get("/stream", summary="Live MJPEG stream of the DUT screen")
async def stream(request: Request) -> StreamingResponse:
    """Stream frames as multipart/x-mixed-replace (viewable in a browser)."""
    hub = _hub(request)
    queue = hub.subscribe()

    async def _frames() -> AsyncIterator[bytes]:
        try:
            while True:
                _, frame = await queue.get()
                yield (
                    b"--" + STREAM_BOUNDARY + b"\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: "
                    + str(len(frame)).encode()
                    + b"\r\n\r\n"
                    + frame
                    + b"\r\n"
                )
        finally:
            hub.unsubscribe(queue)

    return StreamingResponse(
        _frames(),
        media_type=(
            "multipart/x-mixed-replace; boundary="
            + STREAM_BOUNDARY.decode()
        ),
    )


@router.post("/record/start", summary="Start recording to an MKV file")
async def record_start(
    request: Request, name: Optional[str] = Query(default=None)
) -> "dict[str, Any]":
    try:
        path = await _recorder(request).start(name)
    except RecorderBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"file": str(path)}


@router.post("/record/stop", summary="Stop and finalize the recording")
async def record_stop(request: Request) -> "dict[str, Any]":
    path = await _recorder(request).stop()
    return {"file": str(path) if path is not None else None}


@router.get("/status", summary="Capture pipeline status")
async def status(request: Request) -> "dict[str, Any]":
    hub = _hub(request)
    recorder = _recorder(request)
    age = time.time() - hub.latest_ts if hub.latest_ts else None
    return {
        "fps": round(hub.measured_fps, 2),
        "last_frame_age_s": round(age, 3) if age is not None else None,
        "recording": str(recorder.path) if recorder.active else None,
        "device": hub.device or hub.cfg.device,
    }


def create_app(cfg: CaptureConfig) -> FastAPI:
    """Build the relay-capture FastAPI application."""

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        hub = FrameHub(cfg)
        app.state.capture_config = cfg
        app.state.hub = hub
        app.state.recorder = Recorder(hub, cfg)
        task = asyncio.create_task(hub.run())
        try:
            yield
        finally:
            await app.state.recorder.stop()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        title="relay-capture API",
        description="HDMI screen capture of the device under test.",
        version="0.1.0",
        lifespan=_lifespan,
    )
    app.include_router(router)
    return app
