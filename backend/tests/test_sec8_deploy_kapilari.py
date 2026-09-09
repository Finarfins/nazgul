"""Static asserts and startup guard tests for SEC-8 deployment hardening.

Gates:
1. Production Caddy image pinned by sha256 digest in docker-compose.prod.yml.
2. No published database ports or internal app ports in production compose.
3. Caddyfile disables admin API (admin off).
4. No wildcard or plaintext HTTP CORS origins in production compose or .env.production.example.
5. Startup guard: cors_origin_list never contains '*' when environment == 'production'.
6-8. Mutation-style negative tests asserting commented-out variants fail.
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


def _yorumsuz(metin: str) -> str:
    """Strips #... to end of line (respecting nothing fancier)."""
    return "\n".join(line.split("#", 1)[0] for line in metin.splitlines())


def test_caddy_image_pinned_by_digest(content: str | None = None) -> None:
    """Caddy image in docker-compose.prod.yml must be pinned by sha256 digest (stripped of comments)."""
    if content is None:
        content = COMPOSE_PROD.read_text(encoding="utf-8")
    stripped = _yorumsuz(content)

    caddy_images = [m.strip() for m in re.findall(r"image:\s*(caddy\S*)", stripped)]
    assert len(caddy_images) > 0, (
        "docker-compose.prod.yml proxy servisinde yorumsuz 'caddy:...' imajı bulunamadı"
    )

    pattern = r"^caddy:[^\s#@]+@sha256:[a-f0-9]{64}$"
    for img in caddy_images:
        assert re.match(pattern, img) is not None, (
            f"docker-compose.prod.yml proxy servisinde sha256 digest ile sabitlenmemiş Caddy imajı: {img}"
        )
        assert img.startswith("caddy:2.8-alpine@sha256:"), f"Beklenmeyen Caddy imajı: {img}"


def test_production_compose_publishes_no_database_or_app_ports(
    prod_content: str | None = None,
    base_content: str | None = None,
) -> None:
    """Production stack must publish NO database ports and override app port publish (stripped of comments)."""
    if prod_content is None:
        prod_content = COMPOSE_PROD.read_text(encoding="utf-8")
    if base_content is None:
        base_content = COMPOSE_BASE.read_text(encoding="utf-8")

    stripped_prod = _yorumsuz(prod_content)
    stripped_base = _yorumsuz(base_content)

    # Base docker-compose.yml must not publish DB port
    assert "5432:5432" not in stripped_base, "Base compose DB portunu dışarı açmamalı"

    # docker-compose.prod.yml must override app ports to empty list
    assert "ports: !override []" in stripped_prod, (
        "Production app servisi 'ports: !override []' ile portu düşürmeli"
    )

    # Only proxy should have published host ports (80, 443)
    published_port_lines = [
        line.strip()
        for line in stripped_prod.splitlines()
        if re.match(r'^\s*-\s*"\d+:\d+', line)
    ]
    allowed_ports = {'- "80:80"', '- "443:443"', '- "443:443/udp"'}
    assert set(published_port_lines) == allowed_ports, (
        f"Production compose'da izin verilmeyen yayınlanmış portlar: {set(published_port_lines) - allowed_ports}"
    )


def test_caddyfile_admin_api_disabled(content: str | None = None) -> None:
    """Caddyfile must explicitly turn admin API off to prevent opening port 2019 (stripped of comments)."""
    if content is None:
        content = CADDYFILE.read_text(encoding="utf-8")
    stripped = _yorumsuz(content)

    assert re.search(r"^\s*admin\s+off\b", stripped, re.MULTILINE) is not None, (
        "Caddyfile içinde yorumsuz 'admin off' tanımlı olmalı"
    )

    admin_matches = re.findall(r"^\s*admin\s+(.+)$", stripped, re.MULTILINE)
    for directive in admin_matches:
        assert directive.strip() == "off", (
            f"Caddyfile içinde 'admin off' dışında aktif admin yönergesi bulundu: admin {directive.strip()}"
        )


def test_prod_env_and_compose_cors_has_no_wildcard_or_insecure_defaults(
    prod_content: str | None = None,
    env_example: str | None = None,
) -> None:
    """Production compose and example env must derive CORS from HTTPS APP_DOMAIN without wildcard (stripped)."""
    if prod_content is None:
        prod_content = COMPOSE_PROD.read_text(encoding="utf-8")
    if env_example is None:
        env_example = ENV_PROD_EXAMPLE.read_text(encoding="utf-8")

    stripped_prod = _yorumsuz(prod_content)
    stripped_env = _yorumsuz(env_example)

    # Compose derives CORS_ORIGINS using https://${APP_DOMAIN...}
    cors_matches = re.findall(r"^\s*CORS_ORIGINS:\s*(.+)$", stripped_prod, re.MULTILINE)
    assert len(cors_matches) > 0, "docker-compose.prod.yml içinde aktif CORS_ORIGINS bulunamadı"
    for raw_val in cors_matches:
        cors_val = raw_val.strip().strip('"').strip("'")
        assert cors_val.startswith("https://"), f"CORS_ORIGINS https:// ile başlamalı: {cors_val}"
        assert "*" not in cors_val, f"CORS_ORIGINS wildcard içeremez: {cors_val}"

    # .env.production.example must not define an active wildcard or plaintext http CORS_ORIGINS
    env_cors_matches = re.findall(r"^\s*CORS_ORIGINS\s*=\s*(.+)$", stripped_env, re.MULTILINE)
    for raw_val in env_cors_matches:
        val = raw_val.strip().strip('"').strip("'")
        assert "*" not in val, f".env.production.example wildcard CORS içeremez: {val}"
        assert not any(origin.strip().startswith("http://") for origin in val.split(",")), (
            f".env.production.example güvenli olmayan http:// CORS içeremez: {val}"
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


# ============================================================================
# Mutation-style negative tests (commented-out variant regression tests)
# ============================================================================


def test_mutation_caddy_image_commented_fails() -> None:
    """Commented Caddy digest + active unpinned image must fail the gate."""
    orig = COMPOSE_PROD.read_text(encoding="utf-8")
    mutated = re.sub(
        r"image:\s*caddy:[^\s#@]+@sha256:[a-f0-9]{64}",
        "# image: caddy:2.8-alpine@sha256:af32e97399febea808609119bb21544d0265c58a02836576e32a2d082c262c17\n    image: caddy:latest",
        orig,
    )
    assert mutated != orig, "Mutation replacement failed to match"
    with pytest.raises(AssertionError):
        test_caddy_image_pinned_by_digest(mutated)


def test_mutation_caddyfile_admin_off_commented_fails() -> None:
    """Commented admin off + active admin bind must fail the gate."""
    orig = CADDYFILE.read_text(encoding="utf-8")
    mutated = re.sub(
        r"admin\s+off",
        "# admin off\n\tadmin 0.0.0.0:2019",
        orig,
    )
    assert mutated != orig, "Mutation replacement failed to match"
    with pytest.raises(AssertionError):
        test_caddyfile_admin_api_disabled(mutated)


def test_mutation_prod_cors_wildcard_commented_fails() -> None:
    """Commented secure CORS + active wildcard CORS must fail the gate."""
    orig = COMPOSE_PROD.read_text(encoding="utf-8")
    mutated = re.sub(
        r"CORS_ORIGINS:\s*[^\n]+",
        '# CORS_ORIGINS: https://${APP_DOMAIN:-sungurtarim.com}\n      CORS_ORIGINS: "*"',
        orig,
    )
    assert mutated != orig, "Mutation replacement failed to match"
    with pytest.raises(AssertionError):
        test_prod_env_and_compose_cors_has_no_wildcard_or_insecure_defaults(prod_content=mutated)
