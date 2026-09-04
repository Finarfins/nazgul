from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.auth import required_permission

BACKEND = Path(__file__).resolve().parent


def test_supplier_price_import_rbac_map() -> None:
    assert required_permission("POST", "/api/supplier-price-lists/import") == "purchases"


def test_supplier_price_import_sqlite(tmp_path: Path) -> None:
    database = tmp_path / "supplier-price-import.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", SCENARIO],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(BACKEND),
    )
    assert "SUPPLIER_PRICE_IMPORT_OK" in completed.stdout, completed.stdout + completed.stderr


SCENARIO = r'''
from decimal import Decimal
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app


def dec(value):
    return Decimal(str(value))


def csv_upload(client, headers, supplier_id, content):
    return client.post(
        '/api/supplier-price-lists/import',
        headers=headers,
        data={'supplier_id':str(supplier_id)},
        files={'file':('liste.csv', content.encode('utf-8'), 'text/csv')},
    )


def xlsx_upload(client, headers, supplier_id, rows):
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return client.post(
        '/api/supplier-price-lists/import',
        headers=headers,
        data={'supplier_id':str(supplier_id)},
        files={'file':('liste.xlsx', output.getvalue(),
                       'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
    )


# Paylasilan PG veritabaninda bu ailenin smoke'lari ayni bootstrap
# kullanicisini kullanip sifreyi donduruyor. Sabit 'admin123' bu smoke'u
# ILK kosmaya bagimli kiliyordu (ters sirada: "Kullanici adi veya sifre
# hatali"). Bilinen tum sifreler sirayla denenir. (Olculdu.)
BILINEN_SIFRELER = ('admin123', 'admin123Changed!', 'SupplierImport123!',
                    'SupplierBridge123!', 'ImportReport123!', 'ImportReport456!',
                    'BridgeAccounting123!', 'BridgeAccounting456!')

with TestClient(app) as client:
    for _aday in BILINEN_SIFRELER:
        login = client.post('/api/auth/login', json={'username':'admin','password':_aday})
        if login.status_code == 200:
            break
    assert login.status_code == 200, login.text
    body = login.json()
    company_a = body['companies'][0]['id']
    headers = {
        'Authorization':'Bearer '+body['access_token'],
        'X-Company-ID':str(company_a),
    }
    if _aday != 'SupplierImport123!':
        changed = client.post('/api/auth/change-password', headers=headers, json={
            'current_password':_aday,'new_password':'SupplierImport123!',
        })
        assert changed.status_code == 200, changed.text
        headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

    supplier = client.post('/api/suppliers', headers=headers, json={'name':'Liste Tedarikçisi'}).json()['id']
    product_try = client.post('/api/products', headers=headers, json={
        'name':'TRY Filtre','barcode':'869000000001','purchase_price':'1',
    }).json()['id']
    product_eur = client.post('/api/products', headers=headers, json={
        'name':'EUR Filtre','barcode':'869000000002','purchase_price':'1',
    }).json()['id']
    assert client.put('/api/exchange-rates/override', headers=headers, json={
        'currency':'EUR','rate_to_try':'35',
    }).status_code == 200

    company_b = client.post('/api/companies', headers=headers, json={'name':'Import Firma B'}).json()['id']
    headers_b = {**headers, 'X-Company-ID':str(company_b)}
    foreign_supplier = client.post('/api/suppliers', headers=headers_b, json={'name':'Yabancı Tedarikçi'}).json()['id']
    foreign_product = client.post('/api/products', headers=headers_b, json={
        'name':'Yabancı Ürün','barcode':'869999999999','purchase_price':'1',
    }).json()['id']

    first = csv_upload(client, headers, supplier, (
        'Barkod;Fiyat;Para Birimi;MOQ;Teslim Süresi Gün\n'
        '869000000001;12,35;TRY;5;3\n'
        '869000000002;3,00;EUR;10;7\n'
        '869999999999;99,00;TRY;;\n'
        '869000000404;geçersiz;TRY;;\n'
    ))
    assert first.status_code == 200, first.text
    summary = first.json()
    assert summary['processed_rows'] == 4
    assert summary['matched'] == 2 and summary['inserted'] == 2 and summary['updated'] == 0
    assert summary['unmatched_count'] == 1
    assert summary['unmatched_barcodes'] == ['869999999999']
    assert summary['invalid_price_count'] == 1
    assert summary['foreign_currency_rows'] == 1
    assert summary['currencies_requiring_rate'] == ['EUR']

    with SessionLocal() as db:
        rows = db.execute(text("""
            SELECT product_id,price,currency,moq,lead_time_days
            FROM supplier_product_prices
            WHERE company_id=:cid AND supplier_id=:supplier_id
            ORDER BY product_id
        """), {'cid':company_a,'supplier_id':supplier}).mappings().all()
        assert len(rows) == 2
        by_product = {row['product_id']:row for row in rows}
        assert dec(by_product[product_try]['price']) == Decimal('12.35')
        assert dec(by_product[product_try]['moq']) == Decimal('5.0000')
        assert by_product[product_try]['lead_time_days'] == 3
        assert by_product[product_eur]['currency'] == 'EUR'
        assert dec(by_product[product_eur]['price']) == Decimal('3.00')
        assert foreign_product not in by_product

    comparison = client.get('/api/purchase-comparison', headers=headers, params={'q':'EUR Filtre'})
    assert comparison.status_code == 200, comparison.text
    item = next(row for row in comparison.json()['items'] if row['product_id'] == product_eur)
    assert dec(item['offers'][str(supplier)]['price_in_try']) == Decimal('105')

    # Excel uses the same bounded parser/mapping path and updates, rather than
    # duplicating, the existing supplier+product row.
    second = xlsx_upload(client, headers, supplier, [
        ['Barkod','Fiyat','Para Birimi','MOQ','Teslim Süresi Gün'],
        ['869000000001','14.5670','TRY','6','4'],
    ])
    assert second.status_code == 200, second.text
    assert second.json()['inserted'] == 0 and second.json()['updated'] == 1
    with SessionLocal() as db:
        updated = db.execute(text("""
            SELECT price,moq,lead_time_days FROM supplier_product_prices
            WHERE company_id=:cid AND supplier_id=:supplier_id AND product_id=:product_id
        """), {'cid':company_a,'supplier_id':supplier,'product_id':product_try}).mappings().one()
        assert dec(updated['price']) == Decimal('14.57')
        assert dec(updated['moq']) == Decimal('6.0000')
        assert updated['lead_time_days'] == 4
        count = db.execute(text("""
            SELECT COUNT(*) FROM supplier_product_prices
            WHERE company_id=:cid AND supplier_id=:supplier_id AND product_id=:product_id
        """), {'cid':company_a,'supplier_id':supplier,'product_id':product_try}).scalar_one()
        assert count == 1

    # Empty prices are invalid, never Decimal zero. CSV whitespace, a genuinely
    # blank Excel cell, and a formula without a cached data_only value all take
    # the same no-write path and cannot overwrite the existing price.
    blank_csv = csv_upload(client, headers, supplier, (
        'Barkod;Fiyat\n869000000001;   \n'
    ))
    assert blank_csv.status_code == 200, blank_csv.text
    assert blank_csv.json()['matched'] == 0
    assert blank_csv.json()['invalid_price_count'] == 1
    assert blank_csv.json()['invalid_rows'][0]['message'] == 'Fiyat boş.'

    blank_excel = xlsx_upload(client, headers, supplier, [
        ['Barkod','Fiyat'],
        ['869000000001',None],
        ['869000000001','=1+1'],
    ])
    assert blank_excel.status_code == 200, blank_excel.text
    assert blank_excel.json()['matched'] == 0
    assert blank_excel.json()['invalid_price_count'] == 2
    assert {row['message'] for row in blank_excel.json()['invalid_rows']} == {'Fiyat boş.'}
    with SessionLocal() as db:
        after_blanks = db.execute(text("""
            SELECT price FROM supplier_product_prices
            WHERE company_id=:cid AND supplier_id=:supplier_id AND product_id=:product_id
        """), {'cid':company_a,'supplier_id':supplier,'product_id':product_try}).scalar_one()
        assert dec(after_blanks) == Decimal('14.57')

    # Legacy duplicate barcodes are ambiguous. Fail closed instead of choosing
    # an arbitrary product; this shared scenario runs on both SQLite and PG16.
    duplicate_product_response = client.post('/api/products', headers=headers, json={
        'name':'İkinci Aynı Barkod','barcode':'869000000001','purchase_price':'1',
    })
    assert duplicate_product_response.status_code == 201, duplicate_product_response.text
    ambiguous = csv_upload(client, headers, supplier, (
        'Barkod,Fiyat\n869000000001,88.00\n'
    ))
    assert ambiguous.status_code == 200, ambiguous.text
    assert ambiguous.json()['matched'] == 0
    assert ambiguous.json()['inserted'] == 0 and ambiguous.json()['updated'] == 0
    assert ambiguous.json()['invalid_rows'] == [{
        'row':2,'barcode':'869000000001','message':'Barkod birden fazla ürüne ait.',
    }]
    with SessionLocal() as db:
        after_ambiguous = db.execute(text("""
            SELECT price FROM supplier_product_prices
            WHERE company_id=:cid AND supplier_id=:supplier_id AND product_id=:product_id
        """), {'cid':company_a,'supplier_id':supplier,'product_id':product_try}).scalar_one()
        assert dec(after_ambiguous) == Decimal('14.57')

    # File-level validation happens before writes: an unsupported currency in a
    # later row rolls the whole request back, including the otherwise valid row.
    rejected = csv_upload(client, headers, supplier, (
        'Barkod,Fiyat,Para Birimi\n'
        '869000000001,99.00,TRY\n'
        '869000000002,2.00,GBP\n'
    ))
    assert rejected.status_code == 400, rejected.text
    with SessionLocal() as db:
        unchanged = db.execute(text("""
            SELECT price FROM supplier_product_prices
            WHERE company_id=:cid AND supplier_id=:supplier_id AND product_id=:product_id
        """), {'cid':company_a,'supplier_id':supplier,'product_id':product_try}).scalar_one()
        assert dec(unchanged) == Decimal('14.57')

    # A foreign supplier cannot be targeted, and a foreign-only barcode was
    # already reported unmatched rather than resolving across tenant bounds.
    foreign_target = csv_upload(client, headers, foreign_supplier, (
        'Barkod,Fiyat\n869000000001,1.00\n'
    ))
    assert foreign_target.status_code == 404, foreign_target.text

    created = client.post('/api/users', headers=headers, json={
        'username':'import_report','display_name':'Import Report',
        'password':'ImportReport123!','role':'rapor',
    })
    assert created.status_code in (200, 201), created.text
    report_login = client.post('/api/auth/login', json={
        'username':'import_report','password':'ImportReport123!',
    }).json()
    report_headers = {
        'Authorization':'Bearer '+report_login['access_token'],
        'X-Company-ID':str(company_a),
    }
    rotated = client.post('/api/auth/change-password', headers=report_headers, json={
        'current_password':'ImportReport123!','new_password':'ImportReport456!',
    }).json()
    report_headers['Authorization'] = 'Bearer ' + rotated['access_token']
    denied = csv_upload(client, report_headers, supplier, 'Barkod,Fiyat\n869000000001,1.00\n')
    assert denied.status_code == 403, denied.text

print('SUPPLIER_PRICE_IMPORT_OK')
'''
