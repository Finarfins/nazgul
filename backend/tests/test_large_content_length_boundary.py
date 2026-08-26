from __future__ import annotations

import json

import pytest

from app.request_limits import RequestBodyLimitMiddleware


async def _execute(content_length: bytes, *, max_body_bytes: int = 1024) -> tuple[list[dict], int]:
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

    middleware = RequestBodyLimitMiddleware(app, max_body_bytes=max_body_bytes)
    await middleware(scope, receive, send)
    return sent, downstream_calls


def _payload(sent: list[dict]) -> dict:
    return json.loads(sent[1]["body"])


@pytest.mark.asyncio
async def test_extremely_long_decimal_length_is_rejected_without_integer_conversion() -> None:
    sent, downstream_calls = await _execute(b"9" * 5000)

    assert downstream_calls == 0
    assert sent[0]["status"] == 413
    assert _payload(sent)["code"] == "REQUEST_TOO_LARGE"
    assert (b"cache-control", b"no-store") in sent[0]["headers"]


@pytest.mark.asyncio
async def test_leading_zeroes_do_not_make_small_length_oversized() -> None:
    sent, downstream_calls = await _execute((b"0" * 5000) + b"1")

    assert downstream_calls == 1
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_declared_length_equal_to_limit_remains_compatible() -> None:
    sent, downstream_calls = await _execute(b"1024")

    assert downstream_calls == 1
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_declared_length_one_byte_above_limit_keeps_413_contract() -> None:
    sent, downstream_calls = await _execute(b"1025")

    assert downstream_calls == 0
    assert sent[0]["status"] == 413
    assert _payload(sent)["code"] == "REQUEST_TOO_LARGE"


@pytest.mark.asyncio
async def test_lexicographic_comparison_handles_non_power_of_ten_limit() -> None:
    accepted, accepted_calls = await _execute(b"8191", max_body_bytes=8191)
    rejected, rejected_calls = await _execute(b"8192", max_body_bytes=8191)

    assert accepted_calls == 1
    assert accepted[0]["status"] == 204
    assert rejected_calls == 0
    assert rejected[0]["status"] == 413
