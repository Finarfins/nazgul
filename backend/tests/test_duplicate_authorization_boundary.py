from __future__ import annotations

import json

import pytest

from app.request_limits import RequestBodyLimitMiddleware


async def _execute(headers: list[tuple[bytes, bytes]]) -> tuple[list[dict], int]:
    sent: list[dict] = []
    downstream_calls = 0
    incoming = [{"type": "http.request", "body": b"{}", "more_body": False}]

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/products/1",
        "raw_path": b"/api/products/1",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
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


def _response_payload(sent: list[dict]) -> dict:
    return json.loads(sent[1]["body"])


@pytest.mark.asyncio
async def test_duplicate_bearer_headers_are_rejected_before_routing() -> None:
    sent, downstream_calls = await _execute(
        [
            (b"authorization", b"Bearer first-token"),
            (b"authorization", b"Bearer second-token"),
        ]
    )

    assert downstream_calls == 0
    assert sent[0]["status"] == 400
    assert _response_payload(sent)["code"] == "AMBIGUOUS_AUTHORIZATION"
    assert (b"cache-control", b"no-store") in sent[0]["headers"]


@pytest.mark.asyncio
async def test_mixed_case_duplicate_authorization_headers_are_rejected() -> None:
    sent, downstream_calls = await _execute(
        [
            (b"Authorization", b"Basic credentials"),
            (b"authorization", b"Bearer api-token"),
        ]
    )

    assert downstream_calls == 0
    assert sent[0]["status"] == 400
    assert _response_payload(sent)["code"] == "AMBIGUOUS_AUTHORIZATION"


@pytest.mark.asyncio
async def test_single_authorization_header_remains_compatible() -> None:
    sent, downstream_calls = await _execute(
        [(b"authorization", b"Bearer api-token")]
    )

    assert downstream_calls == 1
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_duplicate_authorization_is_rejected_before_body_size_parsing() -> None:
    sent, downstream_calls = await _execute(
        [
            (b"authorization", b"Bearer first-token"),
            (b"authorization", b"Bearer second-token"),
            (b"content-length", b"not-a-number"),
        ]
    )

    assert downstream_calls == 0
    assert sent[0]["status"] == 400
    assert _response_payload(sent)["code"] == "AMBIGUOUS_AUTHORIZATION"
