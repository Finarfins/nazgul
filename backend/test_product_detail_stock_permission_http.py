"""Ürün detayı stok yetkisi — gerçek HTTP davranış kanıtı.

``tests/test_product_detail_stock_permission_security.py`` korumanın rotaya
*bağlı* olduğunu yapısal olarak doğrular. Bu dosya ise korumanın gerçekten
**etkili** olduğunu kanıtlar: yetkisiz roller ``GET /api/products/{id}`` için
403 alır, yetkili roller 200.

Neden önemli: ürün detayı ``purchase_history`` (tedarikçi + alış fiyatı),
``commercial_summary.last_purchase_price`` ve ``movements`` döndürür. Bunlar
ticari açıdan hassas alanlar; taban okuma yetkisi (``read``) tek başına
yetmemeli.

Ayrıca listeleme ucunun (``GET /api/products``) aynı guard'a takılmadığı da
doğrulanır — koruma yalnız detay rotasına bağlıdır, aksi halde satış/rapor
rolleri katalog erişimini kaybederdi.

Çalıştırma modeli komşu sözleşme testleriyle aynı: gerçek migration zinciriyle
kurulan temiz bir veritabanına karşı tek izole alt süreç.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


_SMOKE = r'''
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as c:
    def ok(response):
        assert response.status_code < 300, (response.status_code, response.text)
        return response.json() if response.content else None

    login = ok(c.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'}))
    cid = login['companies'][0]['id']
    h = {'Authorization': 'Bearer ' + login['access_token'], 'X-Company-ID': str(cid)}
    rotated = ok(c.post('/api/auth/change-password', headers=h,
                        json={'current_password': 'admin123', 'new_password': 'StokYetki123!'}))
    h['Authorization'] = 'Bearer ' + rotated['access_token']

    product = ok(c.post('/api/products', headers=h, json={
        'name': 'Yetki Testi Parcasi', 'purchase_price': '40', 'sale_price': '100',
        'vat_rate': '20', 'stock': '5', 'unit': 'Adet',
    }))
    pid = product['id']

    # Admin, hassas alanlari gerçekten görüyor -> korunmasi gereken yüzey bu.
    detail = ok(c.get(f'/api/products/{pid}', headers=h))
    assert 'purchase_history' in detail
    assert 'last_purchase_price' in detail['commercial_summary']
    assert 'movements' in detail

    def as_role(username, password, role):
        ok(c.post('/api/users', headers=h, json={
            'username': username, 'display_name': username,
            'password': password, 'role': role,
        }))
        session = ok(c.post('/api/auth/login', json={'username': username, 'password': password}))
        rh = {'Authorization': 'Bearer ' + session['access_token'], 'X-Company-ID': str(cid)}
        spun = ok(c.post('/api/auth/change-password', headers=rh,
                         json={'current_password': password, 'new_password': password + 'x'}))
        rh['Authorization'] = 'Bearer ' + spun['access_token']
        return rh

    # Stok yetkisi OLMAYAN roller: detay 403, liste 200 (guard yalniz detayda).
    for role in ('satis', 'muhasebe', 'rapor'):
        rh = as_role(f'urun_{role}', f'UrunDetay{role.title()}123!', role)
        denied = c.get(f'/api/products/{pid}', headers=rh)
        assert denied.status_code == 403, (role, denied.status_code, denied.text)
        assert 'stok yetkiniz' in denied.text, (role, denied.text)
        listed = c.get('/api/products', headers=rh)
        assert listed.status_code == 200, (role, listed.status_code, listed.text)

    # Stok yetkisi OLAN rol: detay 200 ve hassas alanlar geliyor.
    depo = as_role('urun_depo', 'UrunDetayDepo123!', 'depo')
    allowed = c.get(f'/api/products/{pid}', headers=depo)
    assert allowed.status_code == 200, (allowed.status_code, allowed.text)
    assert 'purchase_history' in allowed.json()

    # Kimliksiz erisim de reddedilir. TestClient bir cookie jar tasidigi icin
    # onceki oturum temizlenmeden yapilan header'siz istek o oturumla gider;
    # kimliksiz durumu olcmek icin cerezleri bosaltmak sart.
    c.cookies.clear()
    anon = c.get(f'/api/products/{pid}')
    assert anon.status_code in (401, 403), (anon.status_code, anon.text)

print('PRODUCT_DETAIL_STOCK_PERMISSION_OK')
'''


def test_product_detail_enforces_stock_permission_over_http(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'stock_perm.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"

    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )

    assert completed.returncode == 0, (completed.stdout or "") + (completed.stderr or "")
    assert "PRODUCT_DETAIL_STOCK_PERMISSION_OK" in completed.stdout
