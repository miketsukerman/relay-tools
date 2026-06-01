"""Reusable HTTP client for the relay-tools daemon."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import httpx

DEFAULT_URL = "http://localhost:8000"


@dataclass(frozen=True)
class ChannelState:
    """State of a single relay channel."""

    channel: int
    on: bool


@dataclass(frozen=True)
class BoardState:
    """State of all relay channels."""

    channels: tuple[ChannelState, ...]

    @property
    def by_channel(self) -> dict[int, bool]:
        return {channel.channel: channel.on for channel in self.channels}


class RelayClientError(RuntimeError):
    """Base exception raised by :class:`RelayClient`."""


class RelayConnectionError(RelayClientError):
    """Raised when the relay daemon cannot be reached."""

    def __init__(self, url: str):
        super().__init__(
            f"Could not connect to relay daemon at {url}.\n"
            "Make sure the daemon is running: relay serve"
        )


class RelayRequestError(RelayClientError):
    """Raised when the relay daemon returns an HTTP error."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Daemon returned HTTP {status_code}: {detail}")


class RelayClient:
    """Small wrapper around the relay daemon HTTP API."""

    def __init__(
        self,
        url: str = DEFAULT_URL,
        *,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self.url = url
        self._client_factory = client_factory
        self._client: httpx.Client | None = None

    def __enter__(self) -> RelayClient:
        self._client = self._client_factory(base_url=self.url)
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
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._get_client().request(method, path, params=params)
            response.raise_for_status()
        except httpx.ConnectError as exc:
            raise RelayConnectionError(self.url) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            try:
                payload = exc.response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                detail = str(payload.get("detail", detail))
            raise RelayRequestError(exc.response.status_code, detail) from exc
        return response.json()

    @staticmethod
    def _parse_channel_state(data: dict[str, Any]) -> ChannelState:
        return ChannelState(channel=int(data["channel"]), on=bool(data["on"]))

    @classmethod
    def _parse_board_state(cls, data: dict[str, Any]) -> BoardState:
        return BoardState(
            channels=tuple(
                cls._parse_channel_state(channel)
                for channel in data.get("channels", [])
            )
        )

    def status(self) -> BoardState:
        return self._parse_board_state(self._request("GET", "/relays"))

    def get_channel(self, channel: int) -> ChannelState:
        return self._parse_channel_state(self._request("GET", f"/relays/{channel}"))

    def on(self, channel: int) -> ChannelState:
        return self._parse_channel_state(self._request("POST", f"/relays/{channel}/on"))

    def off(self, channel: int) -> ChannelState:
        return self._parse_channel_state(
            self._request("POST", f"/relays/{channel}/off")
        )

    def toggle(self, channel: int) -> ChannelState:
        return self._parse_channel_state(
            self._request("POST", f"/relays/{channel}/toggle")
        )

    def press(self, channel: int, duration: float = 0.2) -> ChannelState:
        return self._parse_channel_state(
            self._request(
                "POST",
                f"/relays/{channel}/press",
                params={"duration": duration},
            )
        )

    def all_on(self) -> BoardState:
        return self._parse_board_state(self._request("POST", "/relays/on"))

    def all_off(self) -> BoardState:
        return self._parse_board_state(self._request("POST", "/relays/off"))
