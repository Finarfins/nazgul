"""Tahsilat içe aktarma — eski sistemden bakiye taşımanın tek pratik yolu.

Satış geçmişi taşınabiliyor ama tahsilatlar taşınamıyor: BizimHesap onları tek
tek tutmuyor, yalnız güncel bakiyeyi gösteriyor. Bu yüzden içe aktarılan her
müşteri gerçekte olduğundan borçlu görünüyor ve fark elle kapatılmak zorunda —
50+ kayıt, kuruş hatasına açık.

Bu uç, satır başına MEVCUT tahsilat ucunu (`routers.finance.add`) çağırır; kendi
SQL'ini yazmaz. Testler bunu da kilitler: tahsilat gerçekten bakiyeyi düşürmeli
ve kayıt normal bir ödeme gibi görünmelidir.
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


def payments_xlsx(rows, header=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header or ['Cari','Tarih','Tutar','Yöntem','Not'])
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload(payload):
    return client.post(
        '/api/imports/payments/excel',
        headers=headers,
        files={'file': ('tahsilat.xlsx', payload,
                        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
    )


customer = client.post('/api/customers', headers=headers,
                       json={'name':'Bakiye Düzeltme Carisi'})
assert customer.status_code == 201, customer.text
customer_id = customer.json()['id']
'''


_IMPORTS_AND_LOWERS_BALANCE = r'''
before = client.get(f'/api/customers/{customer_id}', headers=headers).json()['summary']
assert float(before['payment_total']) == 0.0, before

response = upload(payments_xlsx([
    ['Bakiye Düzeltme Carisi', '01.01.2025', 1500.50, 'Nakit', 'Bakiye düzeltme'],
]))
assert response.status_code == 200, response.text
result = response.json()
assert result['inserted'] == 1, result
assert result['errors'] == [], result

after = client.get(f'/api/customers/{customer_id}', headers=headers).json()['summary']
# Tahsilat gerçekten bakiyeyi düşürmeli: içe aktarma ayrıcalıklı bir yol değil,
# normal ödeme ucundan geçiyor.
assert float(after['payment_total']) == 1500.50, after

listed = client.get('/api/payments', headers=headers).json()
row = [p for p in (listed if isinstance(listed, list) else listed.get('items', []))
       if int(p['entity_id']) == customer_id]
assert row, listed
assert row[0]['payment_date'] == '2025-01-01', row[0]
assert row[0]['note'] == 'Bakiye düzeltme', row[0]
assert row[0]['payment_method'] == 'cash', row[0]

print('PAYMENT_IMPORT_OK')
'''


_ROW_ERRORS_DO_NOT_ABORT = r'''
response = upload(payments_xlsx([
    ['Bakiye Düzeltme Carisi', '01.01.2025', 100, 'Nakit', 'iyi satır'],
    ['Olmayan Cari',           '01.01.2025', 200, 'Nakit', 'eşleşmez'],
    ['Bakiye Düzeltme Carisi', '01.01.2025', -50, 'Nakit', 'negatif'],
    ['Bakiye Düzeltme Carisi', 'tarih değil', 300, 'Nakit', 'bozuk tarih'],
    ['Bakiye Düzeltme Carisi', '02.01.2025', 400, 'Havale', 'ikinci iyi satır'],
]))
assert response.status_code == 200, response.text
result = response.json()
# Hatalı satırlar dosyayı düşürmemeli; iyi satırlar yine de girmeli.
assert result['inserted'] == 2, result
assert len(result['errors']) == 3, result
assert 'Olmayan Cari' in result['unmatched_entities'], result

mesajlar = ' | '.join(e['message'] for e in result['errors'])
assert 'eşleşmedi' in mesajlar, mesajlar
# Tahsilat negatif olamaz (PaymentCreate amount gt=0); sessizce ters çevirmek
# bakiyeyi yanlış yöne taşırdı, o yüzden satır reddedilir.
assert 'negatif' in mesajlar.lower(), mesajlar
assert 'Tarih' in mesajlar, mesajlar

summary = client.get(f'/api/customers/{customer_id}', headers=headers).json()['summary']
assert float(summary['payment_total']) == 500.0, summary

print('PAYMENT_IMPORT_PARTIAL_OK')
'''


_REQUIRES_COLUMNS = r'''
response = upload(payments_xlsx([['x', '01.01.2025', 5]], header=['Ad','Tarih','Tutar']))
assert response.status_code == 400, response.text
assert 'Cari' in response.json()['detail'], response.text
print('PAYMENT_IMPORT_HEADER_OK')
'''


def test_payment_import_creates_collections(tmp_path: Path) -> None:
    _run(tmp_path, 'payment-import', _IMPORTS_AND_LOWERS_BALANCE, 'PAYMENT_IMPORT_OK')


def test_payment_import_reports_bad_rows_without_aborting(tmp_path: Path) -> None:
    _run(tmp_path, 'payment-partial', _ROW_ERRORS_DO_NOT_ABORT, 'PAYMENT_IMPORT_PARTIAL_OK')


def test_payment_import_requires_entity_column(tmp_path: Path) -> None:
    _run(tmp_path, 'payment-header', _REQUIRES_COLUMNS, 'PAYMENT_IMPORT_HEADER_OK')
