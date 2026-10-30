"""HTTP client for the relay-capture daemon, modelled on ConsoleClient."""

from __future__ import annotations

from typing import Any, Callable, Optional, Tuple

import httpx

DEFAULT_CAPTURE_URL = "http://localhost:8002"
CAPTURE_URL_ENV = "RELAY_CAPTURE_URL"


class CaptureClientError(RuntimeError):
    """Base exception raised by :class:`CaptureClient`."""


class CaptureConnectionError(CaptureClientError):
    """Raised when the relay-capture daemon cannot be reached."""

    def __init__(self, url: str):
        super().__init__(
            f"Could not connect to relay-capture at {url}.\n"
            "Make sure the daemon is running: relay-capture"
        )


class CaptureRequestError(CaptureClientError):
    """Raised when the relay-capture daemon returns an HTTP error."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"relay-capture returned HTTP {status_code}: {detail}")


class CaptureClient:
    """Small wrapper around the relay-capture HTTP API."""

    def __init__(
        self,
        url: str = DEFAULT_CAPTURE_URL,
        *,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self.url = url
        self._client_factory = client_factory
        self._client: Optional[httpx.Client] = None

    def __enter__(self) -> "CaptureClient":
        self._get_client()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = self._client_factory(base_url=self.url)
        return self._client

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: "dict[str, Any] | None" = None,
        timeout: "float | None" = None,
    ) -> httpx.Response:
        try:
            response = self._get_client().request(
                method, path, params=params, timeout=timeout
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise CaptureConnectionError(self.url) from exc
        except httpx.ConnectError as exc:
            raise CaptureConnectionError(self.url) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            try:
                payload = exc.response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                detail = str(payload.get("detail", detail))
            raise CaptureRequestError(exc.response.status_code, detail) from exc
        return response

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    def snapshot(self, *, timeout: float = 3.0) -> Tuple[bytes, Optional[float]]:
        """Return ``(jpeg_bytes, frame_timestamp)`` of the next frame."""
        response = self._request(
            "GET",
            "/capture/snapshot",
            params={"timeout": timeout},
            timeout=timeout + 10,
        )
        raw_ts = response.headers.get("x-frame-time")
        ts = float(raw_ts) if raw_ts else None
        return response.content, ts

    def record_start(self, name: "str | None" = None) -> "dict[str, Any]":
        params = {"name": name} if name else None
        return self._request(
            "POST", "/capture/record/start", params=params
        ).json()

    def record_stop(self) -> "dict[str, Any]":
        return self._request(
            "POST", "/capture/record/stop", timeout=30.0
        ).json()

    def status(self) -> "dict[str, Any]":
        return self._request("GET", "/capture/status").json()
