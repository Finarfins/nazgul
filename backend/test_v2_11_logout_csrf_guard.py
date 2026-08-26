from __future__ import annotations

import asyncio

import pytest
from fastapi import APIRouter, HTTPException
from fastapi.routing import APIRoute
from starlette.requests import Request

from app import logout_csrf_guard
from app.logout_csrf_guard import install_logout_csrf_guard, require_logout_csrf
from app.routers import auth


def _request(
    *,
    cookie: str | None = None,
    authorization: str | None = None,
    authorization_values: list[str] | None = None,
) -> Request:
    headers = []
    if cookie is not None:
        headers.append((b"cookie", cookie.encode("latin-1")))
    if authorization is not None:
        headers.append((b"authorization", authorization.encode("latin-1")))
    for value in authorization_values or []:
        headers.append((b"authorization", value.encode("latin-1")))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/logout",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
        }
    )


def test_cookie_logout_rejects_missing_or_invalid_csrf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logout_csrf_guard, "csrf_is_valid", lambda request: False)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(require_logout_csrf(
            _request(cookie=f"{logout_csrf_guard.settings.access_cookie_name}=secret")
        ))

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "CSRF doğrulaması başarısız"


def test_refresh_cookie_logout_also_requires_csrf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logout_csrf_guard, "csrf_is_valid", lambda request: False)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(require_logout_csrf(
            _request(cookie=f"{logout_csrf_guard.settings.refresh_cookie_name}=secret")
        ))

    assert exc_info.value.status_code == 403


def test_cookie_logout_accepts_valid_csrf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logout_csrf_guard, "csrf_is_valid", lambda request: True)

    asyncio.run(require_logout_csrf(
        _request(cookie=f"{logout_csrf_guard.settings.access_cookie_name}=secret; csrf_token=proof")
    ))


def test_bearer_only_logout_remains_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    def should_not_be_called(request: Request) -> bool:
        raise AssertionError("CSRF validation must not run for bearer authentication")

    monkeypatch.setattr(logout_csrf_guard, "csrf_is_valid", should_not_be_called)
    asyncio.run(require_logout_csrf(_request(authorization="Bearer api-token")))


def test_bearer_logout_ignores_stale_access_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    def should_not_be_called(request: Request) -> bool:
        raise AssertionError("Bearer precedence must bypass stale access-cookie CSRF policy")

    monkeypatch.setattr(logout_csrf_guard, "csrf_is_valid", should_not_be_called)
    asyncio.run(
        require_logout_csrf(
            _request(
                cookie=f"{logout_csrf_guard.settings.access_cookie_name}=stale-cookie",
                authorization="Bearer api-token",
            )
        )
    )


def test_bearer_logout_ignores_stale_refresh_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    def should_not_be_called(request: Request) -> bool:
        raise AssertionError("Bearer precedence must bypass stale refresh-cookie CSRF policy")

    monkeypatch.setattr(logout_csrf_guard, "csrf_is_valid", should_not_be_called)
    asyncio.run(
        require_logout_csrf(
            _request(
                cookie=f"{logout_csrf_guard.settings.refresh_cookie_name}=stale-cookie",
                authorization="Bearer api-token",
            )
        )
    )


def test_unrelated_cookie_does_not_break_bearer_logout(monkeypatch: pytest.MonkeyPatch) -> None:
    def should_not_be_called(request: Request) -> bool:
        raise AssertionError("Unrelated cookies must not trigger authentication CSRF policy")

    monkeypatch.setattr(logout_csrf_guard, "csrf_is_valid", should_not_be_called)
    asyncio.run(
        require_logout_csrf(
            _request(
                cookie="theme=dark; analytics_id=123",
                authorization="Bearer api-token",
            )
        )
    )


def test_malformed_bearer_does_not_disable_cookie_csrf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logout_csrf_guard, "csrf_is_valid", lambda request: False)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            require_logout_csrf(
                _request(
                    cookie=f"{logout_csrf_guard.settings.access_cookie_name}=secret",
                    authorization="Bearer   ",
                )
            )
        )

    assert exc_info.value.status_code == 403


def test_duplicate_authorization_headers_are_rejected_before_csrf_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def should_not_be_called(request: Request) -> bool:
        raise AssertionError("Ambiguous authorization must fail before CSRF or bearer parsing")

    monkeypatch.setattr(logout_csrf_guard, "csrf_is_valid", should_not_be_called)
    monkeypatch.setattr(logout_csrf_guard, "extract_bearer", should_not_be_called)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            require_logout_csrf(
                _request(
                    cookie="access_token=secret",
                    authorization_values=["Bearer first", "Bearer second"],
                )
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Birden fazla Authorization başlığı kabul edilmez"


def test_duplicate_mixed_authorization_headers_are_rejected() -> None:
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            require_logout_csrf(
                _request(
                    authorization_values=["Basic credentials", "Bearer api-token"],
                )
            )
        )

    assert exc_info.value.status_code == 400


def test_configured_cookie_names_drive_session_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logout_csrf_guard.settings, "access_cookie_name", "erp_access")
    monkeypatch.setattr(logout_csrf_guard.settings, "refresh_cookie_name", "erp_refresh")
    monkeypatch.setattr(logout_csrf_guard, "csrf_is_valid", lambda request: False)

    with pytest.raises(HTTPException):
        asyncio.run(require_logout_csrf(_request(cookie="erp_refresh=secret")))


def test_guard_is_installed_once_on_real_logout_route() -> None:
    matches = [
        route
        for route in auth.router.routes
        if isinstance(route, APIRoute)
        and route.path == "/auth/logout"
        and "POST" in route.methods
    ]

    assert len(matches) == 1
    calls = [dependency.call for dependency in matches[0].dependant.dependencies]
    assert calls.count(require_logout_csrf) == 1


def test_installer_fails_closed_when_logout_route_is_missing() -> None:
    with pytest.raises(RuntimeError, match="Oturum kapatma rotası"):
        install_logout_csrf_guard(APIRouter())
