"""Interactive API docs / OpenAPI schema are disabled in production.

Swagger UI (/docs), ReDoc (/redoc) and the raw schema (/openapi.json) expose the
full route surface, so they must 404 when ENVIRONMENT=production while staying
available in development. The app object is built from settings at import time,
so each environment is exercised in its own subprocess.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent

_CHECK = r'''
import os
from fastapi.testclient import TestClient
from app.main import app

c = TestClient(app)
gated = os.environ["EXPECT_GATED"] == "1"

def schema_served(resp):
    # True only when the real OpenAPI schema is returned. In production the SPA
    # catch-all serves an HTML shell for /openapi.json (200 but not the schema),
    # so status alone is not sufficient -- assert on the actual payload.
    if resp.status_code != 200 or "application/json" not in resp.headers.get("content-type", ""):
        return False
    try:
        data = resp.json()
    except Exception:
        return False
    return isinstance(data, dict) and "openapi" in data

openapi = c.get("/openapi.json")
docs = c.get("/docs")

if gated:
    assert app.openapi_url is None and app.docs_url is None and app.redoc_url is None, "docs not gated at app level"
    assert not schema_served(openapi), ("openapi schema leaked", openapi.status_code, openapi.headers.get("content-type"))
    assert "swagger-ui" not in docs.text.lower(), "swagger UI served in production"
else:
    assert app.openapi_url == "/openapi.json", app.openapi_url
    assert schema_served(openapi), ("openapi schema missing in development", openapi.status_code)
    assert "swagger-ui" in docs.text.lower(), "swagger UI missing in development"
print("OK")
'''


def _run(env_extra: dict[str, str], tmp_path: Path) -> str:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'docs.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env.update(env_extra)
    result = subprocess.run(
        [sys.executable, "-c", _CHECK],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    return result.stdout


def test_docs_disabled_in_production(tmp_path: Path) -> None:
    out = _run(
        {
            "ENVIRONMENT": "production",
            "COOKIE_SECURE": "true",
            "BOOTSTRAP_ADMIN_PASSWORD": "ProdAdminPass!23",
            "CORS_ORIGINS": "https://erp.example.com",
            "TRUSTED_PROXY_CIDRS": "172.18.0.0/16",
            "TURNSTILE_SECRET_KEY": "test-only-secret",
            "SMTP_HOST": "smtp.ornek.test",
            "SMTP_FROM_EMAIL": "bildirim@ornek.test",
            "NOTIFICATION_PROVIDER": "smtp",
            "PUBLIC_APP_URL": "https://erp.ornek.test",
            "EXPECT_GATED": "1",
        },
        tmp_path,
    )
    assert "OK" in out


def test_docs_enabled_in_development(tmp_path: Path) -> None:
    out = _run(
        {
            "ENVIRONMENT": "development",
            "COOKIE_SECURE": "false",
            "EXPECT_GATED": "0",
        },
        tmp_path,
    )
    assert "OK" in out
