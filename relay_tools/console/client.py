"""HTTP client for the relay-console service, modelled on RelayClient."""

from __future__ import annotations

from typing import Any, Callable

import httpx

DEFAULT_CONSOLE_URL = "http://127.0.0.1:8001"
CONSOLE_URL_ENV = "RELAY_CONSOLE_URL"
CONSOLE_TOKEN_ENV = "RELAY_CONSOLE_TOKEN"
MARKER_TIMEOUT_S = 2.0


class ConsoleClientError(RuntimeError):
    """Base exception raised by :class:`ConsoleClient`."""


class ConsoleConnectionError(ConsoleClientError):
    """Raised when the relay-console service cannot be reached."""

    def __init__(self, url: str):
        super().__init__(
            f"Could not connect to relay-console at {url}.\n"
            "Make sure the service is running: relay-console"
        )


class ConsoleRequestError(ConsoleClientError):
    """Raised when the relay-console service returns an HTTP error."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"relay-console returned HTTP {status_code}: {detail}")


class ConsoleClient:
    """Small wrapper around the relay-console HTTP API."""

    def __init__(
        self,
        url: str = DEFAULT_CONSOLE_URL,
        *,
        token: str | None = None,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self.url = url
        self.token = token
        self._client_factory = client_factory
        self._client: httpx.Client | None = None

    def __enter__(self) -> "ConsoleClient":
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
            headers = {}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            self._client = self._client_factory(
                base_url=self.url, headers=headers
            )
        return self._client

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._get_client().request(
                method,
                path,
                params=params,
                json=json,
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ConsoleConnectionError(self.url) from exc
        except httpx.ConnectError as exc:
            raise ConsoleConnectionError(self.url) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            try:
                payload = exc.response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                detail = str(payload.get("detail", detail))
            raise ConsoleRequestError(exc.response.status_code, detail) from exc
        return response.json()

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    def consoles(self) -> dict[str, Any]:
        return self._request("GET", "/consoles")

    def post_marker(
        self,
        board: str,
        *,
        reason: str,
        actor: str,
        audit_id: str | None = None,
        timeout: float = MARKER_TIMEOUT_S,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/consoles/{board}/markers",
            json={"reason": reason, "actor": actor, "audit_id": audit_id},
            timeout=timeout,
        )

    def boots(self, board: str, *, limit: int = 10) -> dict[str, Any]:
        return self._request(
            "GET", f"/consoles/{board}/boots", params={"limit": limit}
        )

    def log(
        self,
        board: str,
        *,
        boot: str = "last",
        tail: int = 200,
        grep: str | None = None,
        since_line: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"tail": tail}
        if grep is not None:
            params["grep"] = grep
        if since_line:
            params["since_line"] = since_line
        return self._request(
            "GET", f"/consoles/{board}/boots/{boot}/log", params=params
        )

    def report(self, board: str, *, boot: str = "last") -> dict[str, Any]:
        return self._request(
            "GET", f"/consoles/{board}/boots/{boot}/report"
        )

    def wait(
        self,
        board: str,
        *,
        pattern: str,
        timeout_s: float = 120,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/consoles/{board}/wait",
            params={"pattern": pattern, "timeout_s": timeout_s},
            timeout=timeout_s + 10,
        )

    def actions(self, board: str) -> dict[str, Any]:
        return self._request("GET", f"/consoles/{board}/actions")

    def state(self, board: str) -> dict[str, Any]:
        return self._request(
            "POST", f"/consoles/{board}/state", timeout=15.0
        )

    def run_action(
        self,
        board: str,
        name: str,
        *,
        actor: str = "relay-board",
        audit_id: str | None = None,
    ) -> dict[str, Any]:
        headers = {"X-Relay-Actor": actor}
        if audit_id:
            headers["X-Relay-Audit-Id"] = audit_id
        return self._request(
            "POST",
            f"/consoles/{board}/actions/{name}",
            headers=headers,
            timeout=90.0,
        )
