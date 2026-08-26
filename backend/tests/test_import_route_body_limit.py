"""Route-aware body limits: approved import endpoints get a higher cap.

The global boundary default (2 MiB) is below the Excel import endpoints' own
10 MiB streaming limit, which previously made a documented 50k-row product
import (~2.3 MiB) unreachable — the middleware rejected it before the endpoint
could apply its specific message. ``path_overrides`` raises the boundary cap
for approved import routes only; every other route keeps the strict global limit,
and header/framing checks stay global everywhere.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.request_limits import RequestBodyLimitMiddleware

IMPORT_PREFIX = "/api/imports/"
SUPPLIER_PRICE_IMPORT_PATH = "/api/supplier-price-lists/import"
SUPPLIER_PRICE_BRIDGE_IMPORT_PATH = "/api/supplier-prices/imports"
ATTACHMENT_PREFIX = "/api/work-order-attachments/"
OVERRIDES = {IMPORT_PREFIX: 1000}


def _scope(path: str, headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }


async def _run(
    middleware: RequestBodyLimitMiddleware,
    path: str,
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
    await middleware(_scope(path, headers), receive, send)
    return sent, called


async def _draining_app(scope, receive, send) -> None:
    while True:
        message = await receive()
        if message["type"] == "http.disconnect" or not message.get("more_body", False):
            break
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


def _middleware() -> RequestBodyLimitMiddleware:
    return RequestBodyLimitMiddleware(
        _draining_app, max_body_bytes=100, path_overrides=dict(OVERRIDES)
    )


@pytest.mark.asyncio
async def test_import_route_accepts_body_above_global_limit() -> None:
    middleware = _middleware()
    sent, called = await _run(
        middleware,
        "/api/imports/products/excel",
        [{"type": "http.request", "body": b"x" * 500, "more_body": False}],
        headers=[(b"content-length", b"500")],
    )

    assert called is True
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_non_import_route_keeps_global_limit_for_same_size() -> None:
    middleware = _middleware()
    sent, called = await _run(
        middleware,
        "/api/products",
        [{"type": "http.request", "body": b"", "more_body": False}],
        headers=[(b"content-length", b"500")],
    )

    assert called is False
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"])["code"] == "REQUEST_TOO_LARGE"


@pytest.mark.asyncio
async def test_import_route_still_rejects_body_above_its_own_cap() -> None:
    middleware = _middleware()
    sent, called = await _run(
        middleware,
        "/api/imports/products/excel",
        [{"type": "http.request", "body": b"", "more_body": False}],
        headers=[(b"content-length", b"1001")],
    )

    assert called is False
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_import_route_streaming_body_counts_against_override() -> None:
    middleware = _middleware()

    accepted, called_ok = await _run(
        middleware,
        "/api/imports/customers/excel",
        [
            {"type": "http.request", "body": b"a" * 400, "more_body": True},
            {"type": "http.request", "body": b"b" * 400, "more_body": False},
        ],
    )
    assert called_ok is True
    assert accepted[0]["status"] == 204

    rejected, _ = await _run(
        _middleware(),
        "/api/imports/customers/excel",
        [
            {"type": "http.request", "body": b"a" * 600, "more_body": True},
            {"type": "http.request", "body": b"b" * 600, "more_body": False},
        ],
    )
    assert [m.get("status") for m in rejected if m["type"] == "http.response.start"] == [413]


@pytest.mark.asyncio
async def test_override_matches_routing_path_under_asgi_root_path() -> None:
    middleware = _middleware()
    sent: list[dict] = []
    called = False

    async def receive() -> dict:
        return {"type": "http.request", "body": b"x" * 500, "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    original = middleware.app

    async def tracked_app(scope, receive, send) -> None:
        nonlocal called
        called = True
        await original(scope, receive, send)

    middleware.app = tracked_app
    scope = _scope(
        "/erp/api/imports/products/excel", [(b"content-length", b"500")]
    )
    scope["root_path"] = "/erp"
    await middleware(scope, receive, send)

    assert called is True
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_override_prefix_is_anchored_not_substring_matched() -> None:
    middleware = _middleware()
    sent, called = await _run(
        middleware,
        "/api/importsx/excel",
        [{"type": "http.request", "body": b"", "more_body": False}],
        headers=[(b"content-length", b"500")],
    )

    assert called is False
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_longest_matching_prefix_wins() -> None:
    middleware = RequestBodyLimitMiddleware(
        _draining_app,
        max_body_bytes=100,
        path_overrides={"/api/imports/": 1000, "/api/imports/tight/": 200},
    )
    sent, called = await _run(
        middleware,
        "/api/imports/tight/excel",
        [{"type": "http.request", "body": b"", "more_body": False}],
        headers=[(b"content-length", b"500")],
    )

    assert called is False
    assert sent[0]["status"] == 413


def test_invalid_override_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="path_overrides"):
        RequestBodyLimitMiddleware(
            _draining_app, max_body_bytes=100, path_overrides={"/api/imports/": 0}
        )
    with pytest.raises(ValueError, match="path_overrides"):
        RequestBodyLimitMiddleware(
            _draining_app, max_body_bytes=100, path_overrides={"": 10}
        )


@pytest.mark.parametrize("value", [0, 1023, 100 * 1024 * 1024 + 1])
def test_invalid_configured_import_limit_is_rejected(value: int) -> None:
    with pytest.raises(ValidationError, match="MAX_IMPORT_REQUEST_BODY_BYTES"):
        Settings(max_import_request_body_bytes=value)


def test_app_registers_import_override_for_the_import_router() -> None:
    from app import main as app_main
    from app.config import settings

    middleware_entry = next(
        entry
        for entry in app_main.app.user_middleware
        if entry.cls is RequestBodyLimitMiddleware
    )
    overrides = middleware_entry.kwargs["path_overrides"]
    # Exhaustive by design: only approved upload routes may leave the strict
    # global cap, so adding an entry here is an explicit review decision.
    assert overrides == {
        IMPORT_PREFIX: settings.max_import_request_body_bytes,
        SUPPLIER_PRICE_IMPORT_PATH: settings.max_import_request_body_bytes,
        SUPPLIER_PRICE_BRIDGE_IMPORT_PATH: settings.max_supplier_price_import_request_body_bytes,
        ATTACHMENT_PREFIX: settings.max_attachment_upload_bytes + 1024 * 1024,
    }
    # The override must clear the import endpoints' own 10 MiB streaming limit
    # (plus multipart framing) so their specific error messages stay reachable.
    assert settings.max_import_request_body_bytes > 10 * 1024 * 1024
    # Work-order attachments carry their own prefix precisely so this override
    # does not raise the cap for the work-order JSON routes; those must keep the
    # strict global limit.
    assert not any(prefix.startswith("/api/work-orders") for prefix in overrides)


def test_work_order_routes_keep_the_strict_global_body_limit() -> None:
    """The attachment upload cap must not leak onto /api/work-orders/*."""
    from app import main as app_main
    from app.config import settings

    middleware_entry = next(
        entry
        for entry in app_main.app.user_middleware
        if entry.cls is RequestBodyLimitMiddleware
    )
    middleware = RequestBodyLimitMiddleware(
        _draining_app,
        max_body_bytes=middleware_entry.kwargs["max_body_bytes"],
        path_overrides=middleware_entry.kwargs["path_overrides"],
    )
    for path in ("/api/work-orders", "/api/work-orders/1/parts", "/api/work-orders/1/status"):
        assert middleware._body_limit_for(path) == settings.max_request_body_bytes
    assert (
        middleware._body_limit_for("/api/work-order-attachments/1")
        == settings.max_attachment_upload_bytes + 1024 * 1024
    )
