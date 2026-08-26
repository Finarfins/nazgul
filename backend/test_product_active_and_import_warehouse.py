"""Ürünü satışa kapatma + depo yokken anlaşılır içe aktarma hatası.

İki kusur da aynı sınıftan: sistem doğru davranıyor ama kullanıcı onu ne
kontrol edebiliyor ne de neden başarısız olduğunu görebiliyor.

1. ``products.active`` kolonu vardı, POS onu süzüyordu
   (``COALESCE(p.active,TRUE)=TRUE``) ve ürün detayı "Pasif Ürün" rozetini
   gösteriyordu — ama ``ProductUpdate`` şemasında alan olmadığı için değeri
   değiştirmenin hiçbir yolu yoktu. Rozet fiilen hiç görünemezdi.
2. Ürün içe aktarma bir depo ister. Depo yoksa ``default_warehouse``
   ``RuntimeError`` fırlatıyordu; çağrı ``try`` bloğunun dışında olduğu için
   kullanıcı yalnız "Aktarım başarısız." görüyordu ve eksiğin depo olduğunu
   anlayamıyordu. Gerçek bir kurulumda tam olarak bu yaşandı.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


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
import io
import openpyxl
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.main import app
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
headers['Authorization'] = 'Bearer ' + changed.json()['access_token']


def product_xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['Ürün Adı','Ürün Kodu','Satış Fiyatı','Stok','Birim','KDV Oranı'])
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
'''


_ACTIVE_TOGGLE = r'''
created = client.post('/api/products', headers=headers, json={
    'name':'Satışa Kapanacak Parça', 'sale_price':'100.00', 'vat_rate':20,
})
assert created.status_code == 201, created.text
product_id = created.json()['id']

detail = client.get(f'/api/products/{product_id}', headers=headers).json()['product']
# SQLite booleani 1/0, PostgreSQL true/false dondurur; test iki dialekte de
# gecerli olmali (bkz. repodaki PG-parite kurali).
assert bool(detail['active']) is True, detail

# Formun gönderdiği gövde: ürün alanları + aktiflik.
payload = {
    'name': detail['name'], 'sale_price': str(detail['sale_price']),
    'purchase_price': str(detail['purchase_price']), 'vat_rate': detail['vat_rate'],
    'unit': detail['unit'], 'stock': str(detail['stock']),
}
closed = client.put(f'/api/products/{product_id}', headers=headers,
                    json={**payload, 'active': False})
assert closed.status_code == 200, closed.text

with SessionLocal() as db:
    stored = db.execute(text('SELECT active FROM products WHERE id=:i'),
                        {'i': product_id}).scalar()
assert bool(stored) is False, f'satışa kapatma yazılmadı: {stored!r}'

# Kapatma tek yönlü bir kapı olmamalı: geri açılabilmeli.
reopened = client.put(f'/api/products/{product_id}', headers=headers,
                      json={**payload, 'active': True})
assert reopened.status_code == 200, reopened.text
again = client.get(f'/api/products/{product_id}', headers=headers).json()['product']
assert bool(again['active']) is True, again

print('ACTIVE_TOGGLE_OK')
'''


_IMPORT_WITHOUT_WAREHOUSE = r'''
with SessionLocal() as db:
    db.execute(text('DELETE FROM warehouse_stocks'))
    db.execute(text('DELETE FROM warehouses'))
    db.commit()

response = client.post(
    '/api/imports/products/excel',
    headers=headers,
    files={'file': ('urunler.xlsx', product_xlsx([['Test Parça','T-1',100,0,'Adet',20]]),
                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
)
# Eskiden RuntimeError'a düşüp kullanıcıya kuru bir "Aktarım başarısız."
# gösteriyordu; artık ne yapılacağını söyleyen bir 400 dönmeli.
assert response.status_code == 400, f'{response.status_code}: {response.text}'
detail = response.json()['detail']
assert 'depo' in detail.lower(), detail
assert 'Depolar' in detail, f'kullanıcıya hangi ekrana gideceği söylenmeli: {detail}'

print('IMPORT_WAREHOUSE_MESSAGE_OK')
'''


def test_product_can_be_closed_to_sale_and_reopened(tmp_path: Path) -> None:
    _run(tmp_path, 'active-toggle', _ACTIVE_TOGGLE, 'ACTIVE_TOGGLE_OK')


def test_product_import_without_warehouse_explains_itself(tmp_path: Path) -> None:
    _run(tmp_path, 'import-warehouse', _IMPORT_WITHOUT_WAREHOUSE, 'IMPORT_WAREHOUSE_MESSAGE_OK')
