from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_product_router_hides_internal_exception_strings(tmp_path: Path) -> None:
    database = tmp_path / "router-error-sanitization.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)

    smoke = r'''
from fastapi.testclient import TestClient
from app.main import app
from app.routers import products

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
body = login.json()
headers = {'Authorization':'Bearer '+body['access_token'],'X-Company-ID':str(body['companies'][0]['id'])}
changed = client.post('/api/auth/change-password', headers=headers, json={
    'current_password':'admin123','new_password':'AdminRot!2026x'})
assert changed.status_code == 200, changed.text
headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

original = products.sync_product_stock
def boom(*args, **kwargs):
    raise RuntimeError('SECRET_SQL products.internal_column constraint uq_secret')
products.sync_product_stock = boom
try:
    resp = client.post('/api/products', headers=headers, json={
        'name':'Hata Ürün','purchase_price':10,'sale_price':20,'vat_rate':20,'stock':5,'unit':'Adet'})
finally:
    products.sync_product_stock = original

assert resp.status_code == 400, resp.text
assert resp.json()['detail'] == 'İşlem tamamlanamadı. Lütfen tekrar deneyin.', resp.text
assert 'SECRET_SQL' not in resp.text and 'internal_column' not in resp.text and 'uq_secret' not in resp.text, resp.text
client.close()
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke], cwd=BACKEND, env=env,
        text=True, capture_output=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
