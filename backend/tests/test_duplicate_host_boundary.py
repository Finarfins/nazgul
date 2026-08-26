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
        await receive()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestBodyLimitMiddleware(app, max_body_bytes=1024)
    await middleware(scope, receive, send)
    return sent, downstream_calls


def _response_payload(sent: list[dict]) -> dict:
    return json.loads(sent[1]["body"])


@pytest.mark.asyncio
async def test_duplicate_identical_host_headers_are_rejected_before_routing() -> None:
    sent, downstream_calls = await _execute(
        [(b"host", b"erp.example"), (b"host", b"erp.example")]
    )

    assert downstream_calls == 0
    assert sent[0]["status"] == 400
    assert _response_payload(sent)["code"] == "AMBIGUOUS_HOST"
    assert (b"cache-control", b"no-store") in sent[0]["headers"]


@pytest.mark.asyncio
async def test_conflicting_mixed_case_host_headers_are_rejected() -> None:
    sent, downstream_calls = await _execute(
        [(b"Host", b"erp.example"), (b"host", b"attacker.example")]
    )

    assert downstream_calls == 0
    assert sent[0]["status"] == 400
    assert _response_payload(sent)["code"] == "AMBIGUOUS_HOST"


@pytest.mark.asyncio
async def test_single_host_header_remains_compatible() -> None:
    sent, downstream_calls = await _execute([(b"host", b"erp.example")])

    assert downstream_calls == 1
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_duplicate_host_is_rejected_before_authorization_evaluation() -> None:
    sent, downstream_calls = await _execute(
        [
            (b"host", b"erp.example"),
            (b"host", b"attacker.example"),
            (b"authorization", b"Bearer first-token"),
            (b"authorization", b"Bearer second-token"),
        ]
    )

    assert downstream_calls == 0
    assert sent[0]["status"] == 400
    assert _response_payload(sent)["code"] == "AMBIGUOUS_HOST"
