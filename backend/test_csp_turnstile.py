"""Turnstile widget'ının CSP tarafından bloklanmadığını doğrular.

Prod'da ``script-src 'self'`` yüzünden ``challenges.cloudflare.com/turnstile/v0/api.js``
yüklenemiyor, dolayısıyla Register sayfasında jeton üretilemiyor ve "Kayıt ol"
butonu kalıcı olarak inaktif kalıyordu. Bu test hem gerekli üç direktifte host'un
bulunduğunu hem de politikanın gevşetilmediğini (``unsafe-inline`` / ``unsafe-eval``
/ joker karakter yok) kilitler.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent

TURNSTILE_ORIGIN = "https://challenges.cloudflare.com"


def _parse_csp(header: str) -> dict[str, list[str]]:
    directives: dict[str, list[str]] = {}
    for chunk in header.split(";"):
        parts = chunk.split()
        if parts:
            directives[parts[0]] = parts[1:]
    return directives


def test_csp_allows_turnstile_without_relaxing_the_policy(tmp_path: Path) -> None:
    database = tmp_path / "csp-turnstile.db"
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{database.as_posix()}",
            "PYTHONPATH": str(BACKEND),
        }
    )

    script = r'''
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as client:
    response = client.get('/api/health')
    assert response.status_code == 200, response.text
    print('CSP=' + response.headers['content-security-policy'])
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr

    emitted = [line for line in result.stdout.splitlines() if line.startswith("CSP=")]
    assert emitted, result.stdout + "\n" + result.stderr
    header = emitted[-1][len("CSP=") :]
    directives = _parse_csp(header)

    # Turnstile api.js script olarak yüklenir, challenge'ı iframe içinde açar ve
    # doğrulama çağrılarını aynı host'a yapar.
    for directive in ("script-src", "frame-src", "connect-src"):
        assert directive in directives, f"{directive} eksik: {header}"
        assert TURNSTILE_ORIGIN in directives[directive], f"{directive}: {header}"
        # Kendi origin'imiz kaybolmamalı.
        assert "'self'" in directives[directive], f"{directive}: {header}"

    # Politika gevşetilmemeli: script yürütmesi için inline/eval veya joker yok.
    assert "'unsafe-inline'" not in directives["script-src"], header
    assert "'unsafe-eval'" not in directives["script-src"], header
    for directive, sources in directives.items():
        assert "*" not in sources, f"{directive} joker kaynak içeriyor: {header}"

    # Mevcut sertlik korunuyor.
    assert directives["default-src"] == ["'self'"], header
    assert directives["object-src"] == ["'none'"], header
    assert directives["frame-ancestors"] == ["'none'"], header
    assert directives["base-uri"] == ["'self'"], header
    assert directives["form-action"] == ["'self'"], header
