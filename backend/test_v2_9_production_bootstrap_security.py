from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def _run(code: str, **overrides: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(overrides)
    env["PYTHONPATH"] = str(BACKEND)
    env.setdefault("SMTP_HOST", "smtp.ornek.test")
    env.setdefault("SMTP_FROM_EMAIL", "bildirim@ornek.test")
    env.setdefault("NOTIFICATION_PROVIDER", "smtp")
    env.setdefault("PUBLIC_APP_URL", "https://erp.ornek.test")
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )


def test_production_rejects_missing_or_default_bootstrap_password(tmp_path: Path) -> None:
    code = "from app.config import settings; print(settings.environment)"

    missing = _run(
        code,
        ENVIRONMENT="production",
        COOKIE_SECURE="true",
        DATABASE_URL=f"sqlite:///{(tmp_path / 'missing.db').as_posix()}",
        BOOTSTRAP_ADMIN_PASSWORD="",
    )
    assert missing.returncode != 0
    assert "BOOTSTRAP_ADMIN_PASSWORD" in missing.stderr

    default = _run(
        code,
        ENVIRONMENT="production",
        COOKIE_SECURE="true",
        DATABASE_URL=f"sqlite:///{(tmp_path / 'default.db').as_posix()}",
        BOOTSTRAP_ADMIN_PASSWORD="admin123",
    )
    assert default.returncode != 0
    assert "varsayılan parola" in default.stderr


def test_production_bootstrap_uses_configured_secret(tmp_path: Path) -> None:
    database = tmp_path / "production-bootstrap.db"
    secret = "UniqueBootstrapSecret-2026!"
    code = r'''
from sqlalchemy import select
from app.auth import users, verify_password
from app.db import engine
from app.main import app  # noqa: F401 - startup performs migration and bootstrap

with engine.connect() as conn:
    admin = conn.execute(select(users).where(users.c.username == "admin")).mappings().one()
    assert verify_password("UniqueBootstrapSecret-2026!", admin["password_hash"])
    assert not verify_password("admin123", admin["password_hash"])
    assert admin["must_change_password"] is True
print("BOOTSTRAP_SECRET_OK")
'''
    result = _run(
        code,
        ENVIRONMENT="production",
        COOKIE_SECURE="true",
        COOKIE_SAMESITE="lax",
        CORS_ORIGINS="https://erp.example.test",
        DATABASE_URL=f"sqlite:///{database.as_posix()}",
        BOOTSTRAP_ADMIN_PASSWORD=secret,
        TRUSTED_PROXY_CIDRS="172.18.0.0/16",
        TURNSTILE_SECRET_KEY="test-only-secret",
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "BOOTSTRAP_SECRET_OK" in result.stdout


def test_development_keeps_local_first_run_compatibility(tmp_path: Path) -> None:
    database = tmp_path / "development-bootstrap.db"
    code = r'''
from sqlalchemy import select
from app.auth import users, verify_password
from app.db import engine
from app.main import app  # noqa: F401

with engine.connect() as conn:
    admin = conn.execute(select(users).where(users.c.username == "admin")).mappings().one()
    assert verify_password("admin123", admin["password_hash"])
print("DEVELOPMENT_BOOTSTRAP_OK")
'''
    result = _run(
        code,
        ENVIRONMENT="development",
        DATABASE_URL=f"sqlite:///{database.as_posix()}",
        BOOTSTRAP_ADMIN_PASSWORD="",
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "DEVELOPMENT_BOOTSTRAP_OK" in result.stdout
