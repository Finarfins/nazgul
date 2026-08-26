from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def _run_isolated(script: str, database: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["CORS_ORIGINS"] = "http://localhost:5173,http://127.0.0.1:5173"
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )


def test_ready_requires_current_migration_and_security_headers(tmp_path: Path) -> None:
    script = r'''
from fastapi.testclient import TestClient
from app import main

with TestClient(main.app) as client:
    ready = client.get('/api/ready', headers={'X-Request-ID':'qa-check-123'})
    assert ready.status_code == 200, ready.text
    payload = ready.json()
    assert 'migration' not in payload, payload
    assert ready.headers['x-request-id'] == 'qa-check-123'
    assert ready.headers['x-content-type-options'] == 'nosniff'
    assert ready.headers['x-frame-options'] == 'DENY'
    assert ready.headers['referrer-policy'] == 'strict-origin-when-cross-origin'
    assert ready.headers['cross-origin-opener-policy'] == 'same-origin'
    assert ready.headers['cross-origin-resource-policy'] == 'same-origin'
    csp = ready.headers['content-security-policy']
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp

    generated = client.get('/api/live', headers={'X-Request-ID':'bad id with spaces'})
    assert generated.status_code == 200
    assert generated.headers['x-request-id'] != 'bad id with spaces'
    assert len(generated.headers['x-request-id']) == 32

    original = main.alembic_migration_status
    main.alembic_migration_status = lambda _engine: {
        'current':'old-revision','expected':'new-revision','up_to_date':False,
    }
    try:
        stale = client.get('/api/ready')
        assert stale.status_code == 503, stale.text
        assert stale.json()['detail'] == 'Uygulama trafiğe hazır değil'
    finally:
        main.alembic_migration_status = original
'''
    result = _run_isolated(script, tmp_path / "operational.db")
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_cors_wildcard_is_rejected() -> None:
    script = r'''
import os
os.environ['CORS_ORIGINS'] = '*'
try:
    from app.main import app
except ValueError as exc:
    assert 'wildcard' in str(exc)
else:
    raise AssertionError('CORS wildcard kabul edildi')
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        env={**os.environ, "PYTHONPATH": str(BACKEND)},
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
