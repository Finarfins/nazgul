from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.request_limits import RequestBodyLimitMiddleware


def _scope(headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/imports/test",
        "raw_path": b"/api/imports/test",
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }


async def _run(
    middleware: RequestBodyLimitMiddleware,
    messages: list[dict],
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> tuple[list[dict], bool]:
    sent: list[dict] = []
    called = False

    async def receive() -> dict:
        return messages.pop(0)

    async def send(message: dict) -> None:
        sent.append(message)

    original = middleware.app

    async def tracked_app(scope, receive, send) -> None:
        nonlocal called
        called = True
        await original(scope, receive, send)

    middleware.app = tracked_app
    await middleware(_scope(headers), receive, send)
    return sent, called


@pytest.mark.asyncio
async def test_declared_oversized_body_is_rejected_before_downstream_app() -> None:
    async def app(scope, receive, send) -> None:
        raise AssertionError("downstream app must not run")

    middleware = RequestBodyLimitMiddleware(app, max_body_bytes=5)
    sent, called = await _run(
        middleware,
        [{"type": "http.request", "body": b"", "more_body": False}],
        headers=[(b"content-length", b"6")],
    )

    assert called is False
    assert sent[0]["status"] == 413
    payload = json.loads(sent[1]["body"])
    assert payload["code"] == "REQUEST_TOO_LARGE"


@pytest.mark.asyncio
async def test_chunked_body_is_rejected_when_accumulated_size_exceeds_limit() -> None:
    async def app(scope, receive, send) -> None:
        while True:
            message = await receive()
            if message["type"] == "http.disconnect" or not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestBodyLimitMiddleware(app, max_body_bytes=5)
    sent, called = await _run(
        middleware,
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ],
    )

    assert called is True
    assert [message.get("status") for message in sent if message["type"] == "http.response.start"] == [413]


@pytest.mark.asyncio
async def test_body_at_limit_reaches_downstream_app() -> None:
    received = bytearray()

    async def app(scope, receive, send) -> None:
        while True:
            message = await receive()
            received.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestBodyLimitMiddleware(app, max_body_bytes=5)
    sent, called = await _run(
        middleware,
        [{"type": "http.request", "body": b"12345", "more_body": False}],
        headers=[(b"content-length", b"5")],
    )

    assert called is True
    assert bytes(received) == b"12345"
    assert sent[0]["status"] == 204


@pytest.mark.parametrize("value", [0, 1023, 100 * 1024 * 1024 + 1])
def test_invalid_configured_body_limit_is_rejected(value: int) -> None:
    with pytest.raises(ValidationError, match="MAX_REQUEST_BODY_BYTES"):
        Settings(max_request_body_bytes=value)
