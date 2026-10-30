"""Tests for the relay-client capture commands."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from click.testing import CliRunner

from relay_tools.capture.client import DEFAULT_CAPTURE_URL, CaptureClient
from relay_tools.client_cli import client_cli

JPEG = b"\xff\xd8" + b"S" * 16 + b"\xff\xd9"


class _CaptureTransport(httpx.MockTransport):
    def __init__(self, routes):
        self._routes = routes

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key in self._routes:
            return self._routes[key]
        return httpx.Response(404, json={"detail": "not found"})


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _run(runner: CliRunner, routes, *args: str):
    transport = _CaptureTransport(routes)
    with patch("relay_tools.client_cli._capture_client") as factory:
        factory.return_value = CaptureClient(
            DEFAULT_CAPTURE_URL,
            client_factory=lambda **kwargs: httpx.Client(
                transport=transport, **kwargs
            ),
        )
        return runner.invoke(client_cli, ["capture", *args])


def test_capture_snapshot_writes_file(runner: CliRunner, tmp_path):
    routes = {
        ("GET", "/capture/snapshot"): httpx.Response(
            200,
            content=JPEG,
            headers={
                "content-type": "image/jpeg",
                "x-frame-time": "7.000",
            },
        )
    }
    output = tmp_path / "shot.jpg"
    result = _run(runner, routes, "snapshot", "-o", str(output))
    assert result.exit_code == 0, result.output
    assert output.read_bytes() == JPEG
    assert "Snapshot saved" in result.output


def test_capture_snapshot_reports_503(runner: CliRunner, tmp_path):
    routes = {
        ("GET", "/capture/snapshot"): httpx.Response(
            503, json={"detail": "no frame captured within 3.0s"}
        )
    }
    result = _run(
        runner, routes, "snapshot", "-o", str(tmp_path / "shot.jpg")
    )
    assert result.exit_code != 0
    assert "no frame" in result.output


def test_capture_record_start_and_stop(runner: CliRunner):
    routes = {
        ("POST", "/capture/record/start"): httpx.Response(
            200, json={"file": "/var/lib/relay-capture/boot.mkv"}
        ),
        ("POST", "/capture/record/stop"): httpx.Response(
            200, json={"file": "/var/lib/relay-capture/boot.mkv"}
        ),
    }
    result = _run(runner, routes, "record-start", "--name", "boot")
    assert result.exit_code == 0, result.output
    assert "boot.mkv" in result.output

    result = _run(runner, routes, "record-stop")
    assert result.exit_code == 0, result.output
    assert "boot.mkv" in result.output


def test_capture_record_stop_without_recording(runner: CliRunner):
    routes = {
        ("POST", "/capture/record/stop"): httpx.Response(
            200, json={"file": None}
        )
    }
    result = _run(runner, routes, "record-stop")
    assert result.exit_code == 0, result.output
    assert "No recording" in result.output


def test_capture_status(runner: CliRunner):
    routes = {
        ("GET", "/capture/status"): httpx.Response(
            200,
            json={
                "fps": 29.5,
                "last_frame_age_s": 0.03,
                "recording": None,
                "device": "/dev/video0",
            },
        )
    }
    result = _run(runner, routes, "status")
    assert result.exit_code == 0, result.output
    assert "/dev/video0" in result.output
    assert "29.5" in result.output
