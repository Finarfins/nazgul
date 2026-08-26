from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.auth import auth_rate_limits
from app.client_ip import parse_trusted_proxy_networks, resolve_client_host
from app.routers import auth as auth_router
from app.routers.auth import PASSWORD_MIN_LENGTH, RegisterPayload
from app.config import Settings


BASE_PAYLOAD = {
    "company_name": "Test Şirketi",
    "display_name": "Ayşe Test",
    "email": "ayse@example.com",
    "phone": "5361234567",
    "password": "GuvenliParola1!",
    "password_confirmation": "GuvenliParola1!",
    "terms_accepted": True,
}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("company_name", ""),
        ("display_name", ""),
        ("email", ""),
        ("phone", ""),
        ("password", ""),
        ("password_confirmation", ""),
        ("terms_accepted", False),
    ],
)
def test_each_required_registration_field_is_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        RegisterPayload(**{**BASE_PAYLOAD, field: value})


@pytest.mark.parametrize("password", ["", "a", "abcde"])
def test_passwords_below_the_minimum_length_are_rejected(password: str) -> None:
    assert len(password) < PASSWORD_MIN_LENGTH
    with pytest.raises(ValidationError):
        RegisterPayload(
            **{
                **BASE_PAYLOAD,
                "password": password,
                "password_confirmation": password,
            }
        )


@pytest.mark.parametrize(
    "password",
    [
        "abcdef",  # tam minimum uzunluk
        "kucukharf1",
        "BUYUKHARF1",
        "RakamYok",
        "OzelYok1",
    ],
)
def test_passwords_at_or_above_the_minimum_are_accepted(password: str) -> None:
    """Karmaşıklık şartı yok: yalnız uzunluk aranıyor."""
    assert len(password) >= PASSWORD_MIN_LENGTH
    payload = RegisterPayload(
        **{
            **BASE_PAYLOAD,
            "password": password,
            "password_confirmation": password,
        }
    )
    assert payload.password == password


@pytest.mark.parametrize("phone", ["05361234567", "+905361234567"])
def test_prefixed_phone_is_rejected(phone: str) -> None:
    with pytest.raises(ValidationError):
        RegisterPayload(**{**BASE_PAYLOAD, "phone": phone})


def test_ten_digit_phone_is_accepted() -> None:
    assert RegisterPayload(**BASE_PAYLOAD).phone == "5361234567"


def test_registration_email_is_normalized_to_lowercase() -> None:
    payload = RegisterPayload(**{**BASE_PAYLOAD, "email": "  AYSE@EXAMPLE.COM "})
    assert payload.email == "ayse@example.com"


def test_production_requires_trusted_proxy_cidrs() -> None:
    with pytest.raises(
        ValidationError,
        match="Production ortamında TRUSTED_PROXY_CIDRS zorunludur",
    ):
        Settings(
            environment="production",
            cookie_secure=True,
            bootstrap_admin_password="GuvenliBootstrap!2026",
            trusted_proxy_cidrs="",
            turnstile_secret_key="test-secret",
        )


def test_development_without_turnstile_secret_warns_and_allows(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(auth_router.settings, "turnstile_secret_key", None)
    auth_router._verify_turnstile(None, "127.0.0.1")
    assert "TURNSTILE_SECRET_KEY tanımlı değil; bot doğrulaması atlandı" in caplog.text


def test_trusted_proxy_uses_xff_and_untrusted_proxy_ignores_it() -> None:
    trusted = parse_trusted_proxy_networks("172.18.0.0/16")
    assert resolve_client_host("172.18.0.4", "198.51.100.25", trusted) == "198.51.100.25"
    assert resolve_client_host("203.0.113.7", "198.51.100.25", trusted) == "203.0.113.7"


def test_spoofed_xff_cannot_bypass_registration_rate_limit() -> None:
    trusted = parse_trusted_proxy_networks("172.18.0.0/16")
    engine = create_engine("sqlite://")
    auth_rate_limits.create(engine)
    with Session(engine) as db:
        for index in range(5):
            resolved = resolve_client_host(
                "203.0.113.7", f"198.51.100.{index}", trusted
            )
            auth_router._consume_ip_limit(
                db, action="register", ip_address=str(resolved), maximum=5
            )
        with pytest.raises(HTTPException) as exc:
            resolved = resolve_client_host("203.0.113.7", "198.51.100.99", trusted)
            auth_router._consume_ip_limit(
                db, action="register", ip_address=str(resolved), maximum=5
            )
    assert exc.value.status_code == 429


def _migration_module():
    path = (
        Path(__file__).parent
        / "alembic"
        / "versions"
        / "20260729_0040_registration_hardening.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0040", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_engine():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE app_users (id INTEGER PRIMARY KEY, email VARCHAR(320))")
        )
    return engine


def test_migration_stops_with_duplicate_email_ids() -> None:
    engine = _legacy_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO app_users(id,email) VALUES "
                "(2,'same@example.com'),(7,'SAME@example.com')"
            )
        )
        with pytest.raises(RuntimeError, match=r"tekrar eden.*2.*7"):
            _migration_module()._preflight_email_uniqueness(connection)


def test_migration_stops_with_generated_legacy_email_collision_ids() -> None:
    engine = _legacy_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO app_users(id,email) VALUES "
                "(5,NULL),(9,'legacy-user-5@legacy.invalid')"
            )
        )
        with pytest.raises(RuntimeError, match=r"legacy.*5.*9"):
            _migration_module()._preflight_email_uniqueness(connection)
