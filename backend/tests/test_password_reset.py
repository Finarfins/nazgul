"""Şifremi unuttum akışı.

Uygulama alt süreçte, kendi SQLite dosyasıyla ayağa kaldırılır: uçlar gerçek
HTTP üzerinden, gerçek kuyruk ve token tablosuyla denenir. ``test_payments_
import.py`` ile aynı kalıp.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _run(tmp_path: Path, name: str, body: str, marker: str) -> None:
    database = tmp_path / f"{name}.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    result = subprocess.run(
        [sys.executable, "-c", _PRELUDE + body],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert marker in result.stdout, result.stdout + "\n" + result.stderr


_PRELUDE = r'''
import json
from datetime import timedelta
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from app.main import app
from app.auth import password_reset_tokens, users, utcnow
from app.db import SessionLocal

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
body = login.json()
headers = {
    'Authorization': 'Bearer ' + body['access_token'],
    'X-Company-ID': str(body['companies'][0]['id']),
}
changed = client.post('/api/auth/change-password', headers=headers, json={
    'current_password':'admin123', 'new_password':'AdminRot!2026x'
})
assert changed.status_code == 200, changed.text

ADRES = 'patron@ornek-firma.com'
with SessionLocal() as db:
    db.execute(update(users).where(users.c.username == 'admin').values(email=ADRES))
    db.commit()


def sifirlama_baglantilari():
    """Kuyruğa yazılmış sıfırlama satırlarının linklerini döndürür."""
    with SessionLocal() as db:
        rows = db.execute(text(
            "SELECT payload FROM notifications WHERE type='PASSWORD_RESET' "
            "AND status <> 'CANCELLED' ORDER BY id"
        )).scalars().all()
    return [json.loads(row)['reset_url'] for row in rows]


def token_of(link):
    return link.split('token=', 1)[1]


def unuttum(email):
    return client.post('/api/auth/forgot-password', json={'email': email})


def sifirla(token, sifre):
    return client.post('/api/auth/reset-password',
                       json={'token': token, 'new_password': sifre})
'''


_HAPPY_PATH = r'''
assert unuttum(ADRES).status_code == 200
baglantilar = sifirlama_baglantilari()
assert len(baglantilar) == 1, baglantilar

response = sifirla(token_of(baglantilar[0]), 'YeniGuclu!2026')
assert response.status_code == 200, response.text

# Eski şifre ölmeli, yenisi çalışmalı.
eski = client.post('/api/auth/login', json={'username':'admin','password':'AdminRot!2026x'})
assert eski.status_code == 401, eski.text
yeni = client.post('/api/auth/login', json={'username':'admin','password':'YeniGuclu!2026'})
assert yeni.status_code == 200, yeni.text

# Sıfırlama öncesi verilmiş erişim tokenı iptal edilmeli: sıfırlama, hesabın
# ele geçirilmiş olabileceği varsayımıyla tüm cihazları düşürür.
eski_oturum = client.get('/api/auth/me', headers=headers)
assert eski_oturum.status_code == 401, eski_oturum.text

print('RESET_OK')
'''


_TOKEN_IS_SINGLE_USE = r'''
assert unuttum(ADRES).status_code == 200
token = token_of(sifirlama_baglantilari()[0])
assert sifirla(token, 'BirinciSifre!26').status_code == 200

ikinci = sifirla(token, 'IkinciSifre!26')
assert ikinci.status_code == 400, ikinci.text

# İkinci deneme şifreyi DEĞİŞTİRMEMELİ.
kontrol = client.post('/api/auth/login',
                      json={'username':'admin','password':'BirinciSifre!26'})
assert kontrol.status_code == 200, kontrol.text
print('RESET_SINGLE_USE_OK')
'''


_EXPIRED_TOKEN_IS_REJECTED = r'''
assert unuttum(ADRES).status_code == 200
token = token_of(sifirlama_baglantilari()[0])
with SessionLocal() as db:
    db.execute(update(password_reset_tokens).values(
        expires_at=utcnow() - timedelta(minutes=1)))
    db.commit()

response = sifirla(token, 'SuresiDolmus!26')
assert response.status_code == 400, response.text
assert 'süresi dolmuş' in response.json()['detail'], response.text
print('RESET_EXPIRED_OK')
'''


_UNKNOWN_ADDRESS_LEAKS_NOTHING = r'''
bilinen = unuttum(ADRES)
bilinmeyen = unuttum('kimse@ornek-firma.com')
# Aynı durum, aynı gövde: bu uç "bu adres kayıtlı mı" sorusuna cevap vermemeli.
assert bilinen.status_code == bilinmeyen.status_code == 200
assert bilinen.json() == bilinmeyen.json(), (bilinen.json(), bilinmeyen.json())
# Yalnız gerçek adres için kuyruğa satır yazılmış olmalı.
assert len(sifirlama_baglantilari()) == 1, sifirlama_baglantilari()
print('RESET_NO_LEAK_OK')
'''


_LEGACY_PLACEHOLDER_IS_NOT_MAILED = r'''
# 0039 migration'ı e-postasız eski hesaplara "legacy-user-<id>@legacy.invalid"
# yazdı. Bu adres var olmayan bir alan adına gider: postalamak kullanıcıya
# hiçbir şey ulaştırmaz, SES'te sert geri dönüş üretir. Üretimdeki tek hesap
# tam olarak bu durumdaydı.
with SessionLocal() as db:
    db.execute(update(users).where(users.c.username == 'admin')
               .values(email='legacy-user-1@legacy.invalid'))
    db.commit()

response = unuttum('legacy-user-1@legacy.invalid')
assert response.status_code == 200, response.text
assert sifirlama_baglantilari() == [], sifirlama_baglantilari()
print('RESET_LEGACY_SKIPPED_OK')
'''


def test_reset_replaces_password_and_kills_sessions(tmp_path: Path) -> None:
    _run(tmp_path, 'reset-happy', _HAPPY_PATH, 'RESET_OK')


def test_reset_token_is_single_use(tmp_path: Path) -> None:
    _run(tmp_path, 'reset-single', _TOKEN_IS_SINGLE_USE, 'RESET_SINGLE_USE_OK')


def test_expired_reset_token_is_rejected(tmp_path: Path) -> None:
    _run(tmp_path, 'reset-expired', _EXPIRED_TOKEN_IS_REJECTED, 'RESET_EXPIRED_OK')


def test_forgot_password_does_not_leak_account_existence(tmp_path: Path) -> None:
    _run(tmp_path, 'reset-leak', _UNKNOWN_ADDRESS_LEAKS_NOTHING, 'RESET_NO_LEAK_OK')


def test_placeholder_legacy_address_is_never_mailed(tmp_path: Path) -> None:
    _run(
        tmp_path,
        'reset-legacy',
        _LEGACY_PLACEHOLDER_IS_NOT_MAILED,
        'RESET_LEGACY_SKIPPED_OK',
    )
