from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.auth import required_permission

BACKEND = Path(__file__).resolve().parent


def test_quick_pick_rbac_map() -> None:
    assert required_permission("GET", "/api/quick-pick") == "read"


def test_quick_pick_sqlite(tmp_path: Path) -> None:
    database = tmp_path / "quick-pick.db"
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
    assert "QUICK_PICK_OK" in completed.stdout, completed.stdout + completed.stderr


SCENARIO = r'''
from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text
from app.db import SessionLocal, engine
from app.main import app


def dec(value):
    return Decimal(str(value))


with TestClient(app) as client:
    login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {
        'Authorization':'Bearer '+body['access_token'],
        'X-Company-ID':str(body['companies'][0]['id']),
    }
    changed = client.post('/api/auth/change-password', headers=headers, json={
        'current_password':'admin123','new_password':'QuickPick123!',
    })
    assert changed.status_code == 200, changed.text
    headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

    company_a = client.post('/api/companies', headers=headers, json={'name':'Hızlı Seçim A'}).json()['id']
    company_b = client.post('/api/companies', headers=headers, json={'name':'Hızlı Seçim B'}).json()['id']

    def company_headers(company_id):
        result = dict(headers)
        result['X-Company-ID'] = str(company_id)
        return result

    headers_a = company_headers(company_a)
    headers_b = company_headers(company_b)

    def customer(local_headers, name):
        response = client.post('/api/customers', headers=local_headers, json={'name':name})
        assert response.status_code == 201, response.text
        return response.json()['id']

    def product(local_headers, name, code, stock='1000'):
        response = client.post('/api/products', headers=local_headers, json={
            'name':name,'product_code':code,'sale_price':'12.50','stock':stock,'unit':'Adet',
        })
        assert response.status_code == 201, response.text
        return response.json()['id']

    customer_a1 = customer(headers_a, 'Müşteri A1')
    customer_a2 = customer(headers_a, 'Müşteri A2')
    customer_b = customer(headers_b, 'Müşteri B')
    product_a = product(headers_a, 'A Filtre', 'A-1')
    product_b = product(headers_a, 'B Filtre', 'A-2')
    product_c = product(headers_a, 'C Filtre', 'A-3')
    foreign_product = product(headers_b, 'Yabancı Filtre', 'B-1', '500')
    recent = (date.today() - timedelta(days=10)).isoformat()
    old = (date.today() - timedelta(days=220)).isoformat()

    def sale(local_headers, customer_id, product_id, qty, status='completed', when=recent):
        response = client.post('/api/orders', headers=local_headers, json={
            'entity_id':customer_id,'transaction_date':when,'status':status,
            'payment_method':'cash','paid_amount':'0',
            'items':[{'product_id':product_id,'quantity':qty,'unit_price':'12.50','vat_rate':20}],
        })
        assert response.status_code == 201, response.text
        return response.json()['id']

    # A wins on line frequency (3). C and B tie on frequency (2), then C wins
    # on total quantity (25 > 20).
    legacy_order = sale(headers_a, customer_a1, product_a, '1.0000')
    sale(headers_a, customer_a1, product_a, '1.2500')
    sale(headers_a, customer_a2, product_a, '0.7500')
    sale(headers_a, customer_a2, product_b, '9.0000')
    sale(headers_a, customer_a2, product_b, '11.0000')
    sale(headers_a, customer_a2, product_c, '12.0000')
    sale(headers_a, customer_a2, product_c, '13.0000')
    sale(headers_a, customer_a1, product_c, '99.0000', status='cancelled')
    sale(headers_a, customer_a1, product_b, '99.0000', status='draft')
    sale(headers_a, customer_a1, product_b, '99.0000', when=old)
    sale(headers_b, customer_b, foreign_product, '1.0000')
    sale(headers_b, customer_b, foreign_product, '1.0000')
    sale(headers_b, customer_b, foreign_product, '1.0000')
    sale(headers_b, customer_b, foreign_product, '1.0000')

    # Give response stock values a stable, explicit Decimal fixture.
    with SessionLocal() as db:
        default_warehouse_id = db.execute(text(
            'SELECT id FROM warehouses WHERE company_id=:cid AND is_default=TRUE'
        ), {'cid':company_a}).scalar_one()
        for product_id, stock in ((product_a, '7.5000'), (product_b, '8.2500'), (product_c, '9.7500')):
            db.execute(text(
                'UPDATE warehouse_stocks SET quantity=:stock '
                'WHERE product_id=:id AND company_id=:cid AND warehouse_id=:wid'
            ), {'stock':stock,'id':product_id,'cid':company_a,'wid':default_warehouse_id})
        db.commit()

    altered_pg_status = False
    if engine.dialect.name == 'sqlite':
        with engine.begin() as connection:
            # ÖNCE ÇOCUK, SONRA EBEVEYN — ama satırlar KORUNARAK. order_items
            # artık orders'a BİLEŞİK yabancı anahtarla bağlı (göç
            # 20260812_0058); `PRAGMA foreign_keys=ON` altında satırlar
            # dururken orders DÜŞÜRÜLEMEZ. Pragma KAPATILMIYOR: kapatmak, bu
            # dilimin eklediği kısıt dahil dosyadaki tüm FK zorlamasını
            # sessizce iptal ederdi.
            #
            # Satırlar SİLİNİP BIRAKILAMAZ: sezon planı gelirleri uygulama
            # üzerinden order_items'tan topluyor, silinirse testin KENDİ
            # iddiası düşer (ölçüldü). Bu yüzden geçici bir tabloya alınıp
            # ebeveyn yeniden kurulduktan sonra geri konuyorlar.
            #
            # Yeniden kurulan tabloya UNIQUE(company_id, id) da ekleniyor:
            # SQLite bileşik yabancı anahtarın HEDEFİNDE tekil bir indeks
            # arar; olmazsa geri yazma "foreign key mismatch" ile düşer.
            connection.execute(text('CREATE TABLE order_items_stash AS SELECT * FROM order_items'))
            connection.execute(text('DELETE FROM order_items'))
            connection.execute(text('CREATE TABLE orders_legacy AS SELECT * FROM orders'))
            connection.execute(text('DROP TABLE orders'))
            connection.execute(text('ALTER TABLE orders_legacy RENAME TO orders'))
            connection.execute(text(
                'CREATE UNIQUE INDEX uq_orders_company_id ON orders(company_id, id)'))
            connection.execute(text('INSERT INTO order_items SELECT * FROM order_items_stash'))
            connection.execute(text('DROP TABLE order_items_stash'))
    else:
        with engine.begin() as connection:
            nullable = connection.execute(text("""
                SELECT is_nullable FROM information_schema.columns
                WHERE table_schema=current_schema()
                  AND table_name='orders' AND column_name='status'
            """)).scalar_one()
            if nullable == 'NO':
                connection.execute(text('ALTER TABLE orders ALTER COLUMN status DROP NOT NULL'))
                altered_pg_status = True
    with engine.begin() as connection:
        connection.execute(text(
            'UPDATE orders SET status=NULL WHERE id=:id AND company_id=:cid'
        ), {'id':legacy_order,'cid':company_a})

    try:
        client.cookies.clear()
        unauthenticated = client.get('/api/quick-pick')
        assert unauthenticated.status_code == 401, unauthenticated.text

        response = client.get('/api/quick-pick', headers=headers_a, params={'days':180})
        assert response.status_code == 200, response.text
        rows = response.json()
        assert [row['product_id'] for row in rows] == [product_a, product_c, product_b]
        assert [(row['times_sold'], dec(row['total_quantity'])) for row in rows] == [
            (3, Decimal('3.0000')),
            (2, Decimal('25.0000')),
            (2, Decimal('20.0000')),
        ]
        assert dec(rows[0]['current_stock']) == Decimal('7.5000')
        assert dec(rows[0]['unit_price']) == Decimal('12.50')
        assert foreign_product not in [row['product_id'] for row in rows]

        limited = client.get('/api/quick-pick', headers=headers_a, params={'limit':2,'days':180})
        assert limited.status_code == 200, limited.text
        assert [row['product_id'] for row in limited.json()] == [product_a, product_c]

        personalized = client.get('/api/quick-pick', headers=headers_a, params={
            'customer_id':customer_a1,'days':180,
        })
        assert personalized.status_code == 200, personalized.text
        assert [row['product_id'] for row in personalized.json()] == [product_a]
        assert personalized.json()[0]['times_sold'] == 2
        assert dec(personalized.json()[0]['total_quantity']) == Decimal('2.2500')

        foreign_customer = client.get('/api/quick-pick', headers=headers_a, params={
            'customer_id':customer_b,'days':180,
        })
        assert foreign_customer.status_code == 200, foreign_customer.text
        assert foreign_customer.json() == []

        tenant_b = client.get('/api/quick-pick', headers=headers_b, params={'days':180})
        assert tenant_b.status_code == 200, tenant_b.text
        assert [row['product_id'] for row in tenant_b.json()] == [foreign_product]
    finally:
        if engine.dialect.name == 'postgresql':
            with engine.begin() as connection:
                connection.execute(text(
                    "UPDATE orders SET status='completed' WHERE id=:id AND company_id=:cid"
                ), {'id':legacy_order,'cid':company_a})
                if altered_pg_status:
                    connection.execute(text('ALTER TABLE orders ALTER COLUMN status SET NOT NULL'))

print('QUICK_PICK_OK')
'''
