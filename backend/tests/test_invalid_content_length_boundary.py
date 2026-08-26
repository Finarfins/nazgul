from __future__ import annotations

import json

import pytest

from app.request_limits import RequestBodyLimitMiddleware


async def _execute(content_length: bytes) -> tuple[list[dict], int]:
    sent: list[dict] = []
    downstream_calls = 0
    incoming = [{"type": "http.request", "body": b"", "more_body": False}]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/api/products",
        "raw_path": b"/api/products",
        "query_string": b"",
        "headers": [(b"content-length", content_length)],
        "client": ("127.0.0.1", 12345),
        "server": ("erp.example", 443),
    }

    async def receive() -> dict:
        return incoming.pop(0)

    async def send(message: dict) -> None:
        sent.append(message)

    async def app(scope, receive, send) -> None:
        nonlocal downstream_calls
        downstream_calls += 1
        await receive()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestBodyLimitMiddleware(app, max_body_bytes=1024)
    await middleware(scope, receive, send)
    return sent, downstream_calls


def _payload(sent: list[dict]) -> dict:
    return json.loads(sent[1]["body"])


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [b"", b"-1", b"+1", b"1, 1", b"1x"])
async def test_malformed_content_length_is_rejected_before_body_parsing(value: bytes) -> None:
    sent, downstream_calls = await _execute(value)

    assert downstream_calls == 0
    assert sent[0]["status"] == 400
    assert _payload(sent)["code"] == "INVALID_CONTENT_LENGTH"
    assert (b"cache-control", b"no-store") in sent[0]["headers"]


@pytest.mark.asyncio
async def test_ascii_decimal_content_length_remains_compatible() -> None:
    sent, downstream_calls = await _execute(b"0")

    assert downstream_calls == 1
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_oversized_valid_content_length_keeps_413_contract() -> None:
    sent, downstream_calls = await _execute(b"1025")

    assert downstream_calls == 0
    assert sent[0]["status"] == 413
    assert _payload(sent)["code"] == "REQUEST_TOO_LARGE"


@pytest.mark.asyncio
async def test_surrounding_optional_whitespace_is_normalized() -> None:
    sent, downstream_calls = await _execute(b" 0\t")

    assert downstream_calls == 1
    assert sent[0]["status"] == 204
