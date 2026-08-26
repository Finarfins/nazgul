from __future__ import annotations

import json

import pytest

from app.request_limits import RequestBodyLimitMiddleware


async def _execute(headers: list[tuple[bytes, bytes]]) -> tuple[list[dict], int]:
    sent: list[dict] = []
    downstream_calls = 0
    incoming = [{"type": "http.request", "body": b'{}', "more_body": False}]

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/api/auth/login",
        "raw_path": b"/api/auth/login",
        "query_string": b"",
        "headers": headers,
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


def _response_payload(sent: list[dict]) -> dict:
    return json.loads(sent[1]["body"])


@pytest.mark.asyncio
async def test_conflicting_content_type_headers_are_rejected_before_body_parsing() -> None:
    sent, downstream_calls = await _execute(
        [
            (b"content-type", b"application/json"),
            (b"content-type", b"text/plain"),
        ]
    )

    assert downstream_calls == 0
    assert sent[0]["status"] == 400
    assert _response_payload(sent)["code"] == "AMBIGUOUS_CONTENT_TYPE"
    assert (b"cache-control", b"no-store") in sent[0]["headers"]


@pytest.mark.asyncio
async def test_identical_duplicate_content_type_headers_are_rejected() -> None:
    sent, downstream_calls = await _execute(
        [
            (b"Content-Type", b"application/json"),
            (b"content-type", b"application/json"),
        ]
    )

    assert downstream_calls == 0
    assert sent[0]["status"] == 400
    assert _response_payload(sent)["code"] == "AMBIGUOUS_CONTENT_TYPE"


@pytest.mark.asyncio
async def test_single_json_content_type_remains_compatible() -> None:
    sent, downstream_calls = await _execute(
        [(b"content-type", b"application/json; charset=utf-8")]
    )

    assert downstream_calls == 1
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_content_type_ambiguity_precedes_authorization_evaluation() -> None:
    sent, downstream_calls = await _execute(
        [
            (b"content-type", b"application/json"),
            (b"content-type", b"text/plain"),
            (b"authorization", b"Bearer first-token"),
            (b"authorization", b"Bearer second-token"),
        ]
    )

    assert downstream_calls == 0
    assert sent[0]["status"] == 400
    assert _response_payload(sent)["code"] == "AMBIGUOUS_CONTENT_TYPE"
