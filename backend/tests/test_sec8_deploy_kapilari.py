"""Static asserts and startup guard tests for SEC-8 deployment hardening.

Gates:
1. Production Caddy image pinned by sha256 digest in docker-compose.prod.yml.
2. No published database ports or internal app ports in production compose.
3. Caddyfile disables admin API (admin off).
4. No wildcard or plaintext HTTP CORS origins in production compose or .env.production.example.
5. Startup guard: cors_origin_list never contains '*' when environment == 'production'.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_BASE = REPO_ROOT / "docker-compose.yml"
COMPOSE_PROD = REPO_ROOT / "docker-compose.prod.yml"
CADDYFILE = REPO_ROOT / "deploy" / "Caddyfile"
ENV_PROD_EXAMPLE = REPO_ROOT / ".env.production.example"


def test_caddy_image_pinned_by_digest() -> None:
    """Caddy image in docker-compose.prod.yml must be pinned by sha256 digest."""
    content = COMPOSE_PROD.read_text(encoding="utf-8")
    pattern = r"image:\s*(caddy:[^\s#@]+@sha256:[a-f0-9]{64})"
    match = re.search(pattern, content)
    assert match is not None, (
        "docker-compose.prod.yml proxy servisinde 'caddy:...@sha256:...' digest sabitlemesi bulunamadı"
    )
    pinned_image = match.group(1)
    assert pinned_image.startswith("caddy:2.8-alpine@sha256:"), f"Beklenmeyen Caddy imajı: {pinned_image}"


def test_production_compose_publishes_no_database_or_app_ports() -> None:
    """Production stack must publish NO database ports and override app port publish."""
    prod_content = COMPOSE_PROD.read_text(encoding="utf-8")
    base_content = COMPOSE_BASE.read_text(encoding="utf-8")

    # Base docker-compose.yml must not publish DB port
    assert "5432:5432" not in base_content, "Base compose DB portunu dışarı açmamalı"

    # docker-compose.prod.yml must override app ports to empty list
    assert "ports: !override []" in prod_content, "Production app servisi 'ports: !override []' ile portu düşürmeli"

    # Only proxy should have published host ports (80, 443)
    published_port_lines = [
        line.strip()
        for line in prod_content.splitlines()
        if re.match(r'^\s*-\s*"\d+:\d+', line)
    ]
    allowed_ports = {'- "80:80"', '- "443:443"', '- "443:443/udp"'}
    assert set(published_port_lines) == allowed_ports, (
        f"Production compose'da izin verilmeyen yayınlanmış portlar: {set(published_port_lines) - allowed_ports}"
    )


def test_caddyfile_admin_api_disabled() -> None:
    """Caddyfile must explicitly turn admin API off to prevent opening port 2019."""
    content = CADDYFILE.read_text(encoding="utf-8")
    assert re.search(r"admin\s+off", content) is not None, "Caddyfile içinde 'admin off' tanımlı olmalı"


def test_prod_env_and_compose_cors_has_no_wildcard_or_insecure_defaults() -> None:
    """Production compose and example env must derive CORS from HTTPS APP_DOMAIN without wildcard."""
    prod_content = COMPOSE_PROD.read_text(encoding="utf-8")
    env_example = ENV_PROD_EXAMPLE.read_text(encoding="utf-8")

    # Compose derives CORS_ORIGINS using https://${APP_DOMAIN...}
    match = re.search(r"CORS_ORIGINS:\s*([^\n#]+)", prod_content)
    assert match is not None, "docker-compose.prod.yml içinde CORS_ORIGINS bulunamadı"
    cors_val = match.group(1).strip()
    assert cors_val.startswith("https://"), f"CORS_ORIGINS https:// ile başlamalı: {cors_val}"
    assert "*" not in cors_val, f"CORS_ORIGINS wildcard içeremez: {cors_val}"

    # .env.production.example must not define an active wildcard or plaintext http CORS_ORIGINS
    for line in env_example.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("CORS_ORIGINS=") and not trimmed.startswith("#"):
            val = trimmed.split("=", 1)[1]
            assert "*" not in val, f".env.production.example wildcard CORS içeremez: {trimmed}"
            assert not any(origin.startswith("http://") for origin in val.split(",")), (
                f".env.production.example güvenli olmayan http:// CORS içeremez: {trimmed}"
            )


PROD_KWARGS = {
    "cookie_secure": True,
    "cookie_samesite": "lax",
    "bootstrap_admin_password": "UniqueBootstrapSecret-2026!",
    "trusted_proxy_cidrs": "172.18.0.0/16",
    "turnstile_secret_key": "test-only-secret",
    "smtp_host": "smtp.example.com",
    "smtp_from_email": "admin@example.com",
    "notification_provider": "smtp",
    "public_app_url": "https://erp.example.com",
}


def test_cors_origin_list_never_contains_wildcard_in_production() -> None:
    """Startup guard: cors_origin_list never contains '*' when environment == 'production'."""
    # Attempting to configure wildcard CORS in production must fail validation
    with pytest.raises((ValueError, ValidationError)) as exc_info:
        Settings(_env_file=None, environment="production", cors_origins="*", **PROD_KWARGS)
    assert "wildcard" in str(exc_info.value).lower()

    with pytest.raises((ValueError, ValidationError)) as exc_info:
        Settings(_env_file=None, environment="production", cors_origins="https://erp.example.com,*", **PROD_KWARGS)
    assert "wildcard" in str(exc_info.value).lower()

    # Valid origins parse to a list without wildcard
    s = Settings(
        _env_file=None,
        environment="production",
        cors_origins="https://erp.example.com,https://admin.example.com",
        **PROD_KWARGS,
    )
    assert s.is_production is True
    assert s.cors_origin_list == ["https://erp.example.com", "https://admin.example.com"]
    assert "*" not in s.cors_origin_list
