from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import APIRouter, HTTPException
from starlette.requests import Request

from app.user_create_identity_guard import (
    _contains_forbidden_identity_character,
    install_user_create_identity_guard,
    validate_created_user_identity,
)


def _request(payload: object) -> Request:
    body = json.dumps(payload).encode("utf-8")
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/users",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


def _validate(payload: object) -> None:
    asyncio.run(validate_created_user_identity(_request(payload)))


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"username": "   ", "display_name": "Geçerli", "password": "safe-password", "role": "rapor"},
            "Kullanıcı adı",
        ),
        (
            {"username": "ab ", "display_name": "Geçerli", "password": "safe-password", "role": "rapor"},
            "Kullanıcı adı",
        ),
        (
            {"username": "gecerli", "display_name": "   ", "password": "safe-password", "role": "rapor"},
            "Görünen ad",
        ),
        (
            {"username": "gecerli", "display_name": "A ", "password": "safe-password", "role": "rapor"},
            "Görünen ad",
        ),
    ],
)
def test_rejects_identity_that_becomes_too_short_after_trimming(payload, message):
    with pytest.raises(HTTPException) as exc:
        _validate(payload)

    assert exc.value.status_code == 422
    assert message in str(exc.value.detail)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("username", "kullanici\nadmin", "Kullanıcı adı"),
        ("username", "kullanici\tadmin", "Kullanıcı adı"),
        ("username", "kullanici\u200dadmin", "Kullanıcı adı"),
        ("display_name", "Kullanıcı\rYönetici", "Görünen ad"),
        ("display_name", "Kullanıcı\u200bYönetici", "Görünen ad"),
    ],
)
def test_rejects_control_and_invisible_format_characters(field, value, message):
    payload = {
        "username": "gecerli",
        "display_name": "Geçerli Kullanıcı",
        "password": "safe-password",
        "role": "rapor",
    }
    payload[field] = value

    with pytest.raises(HTTPException) as exc:
        _validate(payload)

    assert exc.value.status_code == 422
    assert message in str(exc.value.detail)
    assert "görünmez" in str(exc.value.detail)


@pytest.mark.parametrize("value", ["satış.01", "Çağrı Şahin", "depo-kullanıcısı"])
def test_allows_visible_turkish_identity_characters(value):
    assert _contains_forbidden_identity_character(value) is False


def test_accepts_valid_identity_with_surrounding_whitespace():
    _validate(
        {
            "username": "  kullanici  ",
            "display_name": "  Kullanıcı Adı  ",
            "password": "safe-password",
            "role": "rapor",
        }
    )


def test_installer_attaches_guard_only_to_post_users_route():
    router = APIRouter()

    @router.get("/users")
    def list_users():
        return []

    @router.post("/users")
    def create_user():
        return {"id": 1}

    install_user_create_identity_guard(router)

    post_route = next(route for route in router.routes if "POST" in route.methods)
    get_route = next(route for route in router.routes if "GET" in route.methods)
    assert post_route.dependencies[0].dependency is validate_created_user_identity
    assert get_route.dependencies == []


def test_installer_fails_closed_when_create_route_is_missing():
    router = APIRouter()

    with pytest.raises(RuntimeError, match="Kullanıcı oluşturma rotası"):
        install_user_create_identity_guard(router)
