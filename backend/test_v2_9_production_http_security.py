from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_production_cookie_cors_and_security_headers(tmp_path: Path) -> None:
    database = tmp_path / "production-http-security.db"
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{database.as_posix()}",
            "PYTHONPATH": str(BACKEND),
            "COOKIE_SECURE": "true",
            "COOKIE_SAMESITE": "strict",
            "CORS_ORIGINS": "https://erp.sungurtarim.example",
        }
    )

    smoke = r'''
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import app

client = TestClient(app, base_url='https://erp.sungurtarim.example')

response = client.get('/api/health')
assert response.status_code == 200, response.text
headers = {key.lower(): value for key, value in response.headers.items()}
assert headers['strict-transport-security'] == 'max-age=31536000; includeSubDomains'
assert headers['x-content-type-options'] == 'nosniff'
assert headers['x-frame-options'] == 'DENY'
assert headers['referrer-policy'] == 'strict-origin-when-cross-origin'
assert headers['cross-origin-opener-policy'] == 'same-origin'
assert headers['cross-origin-resource-policy'] == 'same-origin'
assert "frame-ancestors 'none'" in headers['content-security-policy']
assert headers['cache-control'] == 'no-store'

allowed = client.options(
    '/api/auth/login',
    headers={
        'Origin': 'https://erp.sungurtarim.example',
        'Access-Control-Request-Method': 'POST',
    },
)
assert allowed.status_code == 200, allowed.text
assert allowed.headers['access-control-allow-origin'] == 'https://erp.sungurtarim.example'
assert allowed.headers['access-control-allow-credentials'] == 'true'

denied = client.options(
    '/api/auth/login',
    headers={
        'Origin': 'https://evil.example',
        'Access-Control-Request-Method': 'POST',
    },
)
assert denied.headers.get('access-control-allow-origin') is None

login = client.post('/api/auth/login', json={
    'username': 'admin', 'password': 'admin123'
})
assert login.status_code == 200, login.text
cookies = login.headers.get_list('set-cookie')
assert any('yhp_access_token=' in value and 'HttpOnly' in value and 'Secure' in value and 'SameSite=strict' in value for value in cookies)
assert any('yhp_refresh_token=' in value and 'HttpOnly' in value and 'Secure' in value and 'SameSite=strict' in value for value in cookies)
assert any('yhp_csrf_token=' in value and 'Secure' in value and 'SameSite=strict' in value and 'HttpOnly' not in value for value in cookies)

try:
    Settings(_env_file=None, cors_origins='*')
except ValueError as exc:
    assert 'wildcard' in str(exc)
else:
    raise AssertionError('Wildcard CORS configuration must be rejected')

try:
    Settings(_env_file=None, cookie_samesite='none', cookie_secure=False)
except ValidationError as exc:
    assert 'COOKIE_SECURE=true' in str(exc)
else:
    raise AssertionError('SameSite=None without Secure must be rejected')

client.close()
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
