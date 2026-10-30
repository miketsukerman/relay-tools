"""Tests for the dut_screenshot MCP tool."""

from __future__ import annotations

import base64

import httpx
import pytest

pytest.importorskip("mcp")

from relay_tools.mcp.server import (  # noqa: E402
    RelayMCPService,
    Settings,
    ToolError,
    create_server,
)

JPEG = b"\xff\xd8" + b"S" * 16 + b"\xff\xd9"


class FakeCaptureDaemon:
    """In-memory relay-capture daemon implementing /capture/snapshot."""

    def __init__(self, *, frame: "bytes | None" = JPEG) -> None:
        self.frame = frame
        self.requests: "list[httpx.Request]" = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path != "/capture/snapshot":
            return httpx.Response(404, json={"detail": "not found"})
        if self.frame is None:
            return httpx.Response(
                503, json={"detail": "no frame captured within 3.0s"}
            )
        return httpx.Response(
            200,
            content=self.frame,
            headers={
                "content-type": "image/jpeg",
                "x-frame-time": "1234.500",
            },
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def _service(tmp_path, capture: FakeCaptureDaemon, **overrides) -> RelayMCPService:
    transport = capture.transport()
    settings = Settings(
        config_dir=tmp_path / "boards",
        audit_log=tmp_path / "audit.jsonl",
        capture_client_factory=lambda **kwargs: httpx.Client(
            transport=transport, **kwargs
        ),
        **overrides,
    )
    return RelayMCPService(settings)


def test_dut_screenshot_returns_jpeg(tmp_path):
    capture = FakeCaptureDaemon()
    service = _service(tmp_path, capture)
    jpeg, ts = service.dut_screenshot()
    assert jpeg == JPEG
    assert ts == 1234.5


def test_dut_screenshot_allowed_in_read_only_mode(tmp_path):
    capture = FakeCaptureDaemon()
    service = _service(tmp_path, capture, read_only=True)
    jpeg, _ = service.dut_screenshot()
    assert jpeg == JPEG


def test_dut_screenshot_maps_daemon_errors_to_tool_error(tmp_path):
    capture = FakeCaptureDaemon(frame=None)
    service = _service(tmp_path, capture)
    with pytest.raises(ToolError, match="no frame"):
        service.dut_screenshot()


def test_dut_screenshot_rejects_bad_timeout(tmp_path):
    service = _service(tmp_path, FakeCaptureDaemon())
    with pytest.raises(ToolError, match="timeout_s"):
        service.dut_screenshot(timeout_s=0)
    with pytest.raises(ToolError, match="timeout_s"):
        service.dut_screenshot(timeout_s=120)


async def test_dut_screenshot_tool_returns_image_content(tmp_path):
    capture = FakeCaptureDaemon()
    settings = Settings(
        config_dir=tmp_path / "boards",
        audit_log=tmp_path / "audit.jsonl",
        capture_client_factory=lambda **kwargs: httpx.Client(
            transport=capture.transport(), **kwargs
        ),
    )
    server = create_server(settings)
    result = await server.call_tool("dut_screenshot", {})
    if isinstance(result, tuple):  # mcp 1.x: (content, structured)
        contents = result[0]
    elif hasattr(result, "content"):  # mcp 2.x: CallToolResult
        contents = result.content
    else:
        contents = result
    image = contents[0]
    assert image.type == "image"
    mime = getattr(image, "mimeType", None) or getattr(image, "mime_type")
    assert mime == "image/jpeg"
    assert base64.b64decode(image.data) == JPEG
