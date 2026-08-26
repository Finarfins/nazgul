from __future__ import annotations

import json

import pytest

from app.request_limits import RequestBodyLimitMiddleware


async def _execute(headers: list[tuple[bytes, bytes]]) -> tuple[list[dict], int]:
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


def _payload(sent: list[dict]) -> dict:
    return json.loads(sent[1]["body"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        [(b"transfer-encoding", b"chunked"), (b"transfer-encoding", b"chunked")],
        [(b"Transfer-Encoding", b"gzip"), (b"transfer-encoding", b"chunked")],
    ],
)
async def test_duplicate_transfer_encoding_is_rejected_before_body_parsing(
    headers: list[tuple[bytes, bytes]],
) -> None:
    sent, downstream_calls = await _execute(headers)

    assert downstream_calls == 0
    assert sent[0]["status"] == 400
    assert _payload(sent)["code"] == "AMBIGUOUS_REQUEST_FRAMING"
    assert (b"cache-control", b"no-store") in sent[0]["headers"]


@pytest.mark.asyncio
async def test_single_transfer_encoding_remains_compatible() -> None:
    sent, downstream_calls = await _execute([(b"transfer-encoding", b"chunked")])

    assert downstream_calls == 1
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_transfer_encoding_and_content_length_remain_rejected() -> None:
    sent, downstream_calls = await _execute(
        [(b"transfer-encoding", b"chunked"), (b"content-length", b"0")]
    )

    assert downstream_calls == 0
    assert sent[0]["status"] == 400
    assert _payload(sent)["code"] == "AMBIGUOUS_REQUEST_FRAMING"
