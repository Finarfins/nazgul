from __future__ import annotations

import json

import pytest

from app.request_limits import RequestBodyLimitMiddleware


async def _execute(
    headers: list[tuple[bytes, bytes]],
    *,
    max_header_bytes: int = 64,
    scope_type: str = "http",
) -> tuple[list[dict], int]:
    sent: list[dict] = []
    downstream_calls = 0
    incoming = [{"type": "http.request", "body": b"", "more_body": False}]
    scope = {
        "type": scope_type,
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/api/products",
        "raw_path": b"/api/products",
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
        if scope["type"] == "http":
            await receive()
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

    middleware = RequestBodyLimitMiddleware(
        app,
        max_body_bytes=1024,
        max_header_bytes=max_header_bytes,
    )
    await middleware(scope, receive, send)
    return sent, downstream_calls


def _payload(sent: list[dict]) -> dict:
    return json.loads(sent[1]["body"])


@pytest.mark.asyncio
async def test_header_bytes_above_limit_are_rejected_before_downstream() -> None:
    sent, downstream_calls = await _execute([(b"x-test", b"a" * 59)])

    assert downstream_calls == 0
    assert sent[0]["status"] == 431
    assert _payload(sent)["code"] == "REQUEST_HEADERS_TOO_LARGE"
    assert (b"cache-control", b"no-store") in sent[0]["headers"]


@pytest.mark.asyncio
async def test_header_bytes_equal_to_limit_are_accepted() -> None:
    sent, downstream_calls = await _execute([(b"x-test", b"a" * 58)])

    assert downstream_calls == 1
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_aggregate_size_counts_names_and_values_across_headers() -> None:
    sent, downstream_calls = await _execute(
        [(b"x-one", b"a" * 28), (b"x-two", b"b" * 28)]
    )

    assert downstream_calls == 0
    assert sent[0]["status"] == 431
    assert _payload(sent)["code"] == "REQUEST_HEADERS_TOO_LARGE"


@pytest.mark.asyncio
async def test_header_limit_precedes_ambiguous_metadata_checks() -> None:
    sent, downstream_calls = await _execute(
        [(b"host", b"a" * 30), (b"host", b"b" * 30)]
    )

    assert downstream_calls == 0
    assert sent[0]["status"] == 431
    assert _payload(sent)["code"] == "REQUEST_HEADERS_TOO_LARGE"


@pytest.mark.asyncio
async def test_default_limit_allows_ordinary_headers() -> None:
    sent, downstream_calls = await _execute(
        [(b"host", b"erp.example"), (b"accept", b"application/json")],
        max_header_bytes=RequestBodyLimitMiddleware.DEFAULT_MAX_HEADER_BYTES,
    )

    assert downstream_calls == 1
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_non_http_scopes_are_not_subject_to_header_limit() -> None:
    sent, downstream_calls = await _execute(
        [(b"x-test", b"a" * 100)],
        max_header_bytes=1,
        scope_type="websocket",
    )

    assert downstream_calls == 1
    assert sent == []


def test_header_limit_must_be_positive() -> None:
    async def app(scope, receive, send) -> None:
        return None

    with pytest.raises(ValueError, match="max_header_bytes"):
        RequestBodyLimitMiddleware(app, max_body_bytes=1024, max_header_bytes=0)
