"""Tests for the reusable relay HTTP client."""

from __future__ import annotations

import httpx
import pytest

from relay_tools.client import (
    DEFAULT_URL,
    RelayClient,
    RelayConnectionError,
    RelayRequestError,
)


class _MockTransport(httpx.MockTransport):
    def __init__(self, routes):
        self._routes = routes

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        route = self._routes[key]
        if callable(route):
            return route(request)
        return route


def _make_client(routes, *, url: str = DEFAULT_URL) -> RelayClient:
    transport = _MockTransport(routes)
    return RelayClient(
        url,
        client_factory=lambda **kwargs: httpx.Client(transport=transport, **kwargs),
    )


def test_on_returns_channel_state() -> None:
    with _make_client(
        {("POST", "/relays/1/on"): httpx.Response(200, json={"channel": 1, "on": True})}
    ) as client:
        state = client.on(1)
    assert state.channel == 1
    assert state.on is True


def test_status_returns_board_state() -> None:
    with _make_client(
        {
            ("GET", "/relays"): httpx.Response(
                200,
                json={
                    "channels": [
                        {"channel": 1, "on": True},
                        {"channel": 2, "on": False},
                    ]
                },
            )
        }
    ) as client:
        state = client.status()
    assert state.by_channel == {1: True, 2: False}


def test_press_forwards_duration_query_param() -> None:
    calls = []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, str(request.url.params)))
        return httpx.Response(200, json={"channel": 3, "on": False})

    with _make_client({("POST", "/relays/3/press"): _handler}) as client:
        client.press(3, 0.5)

    assert calls == [("/relays/3/press", "duration=0.5")]


def test_http_errors_raise_relay_request_error() -> None:
    with _make_client(
        {("POST", "/relays/99/on"): httpx.Response(404, json={"detail": "missing"})}
    ) as client:
        with pytest.raises(RelayRequestError, match="404: missing"):
            client.on(99)


def test_connect_errors_raise_relay_connection_error() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with _make_client({("GET", "/relays"): _handler}) as client:
        with pytest.raises(RelayConnectionError, match="Could not connect"):
            client.status()
