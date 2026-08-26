from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.auth import required_permission

BACKEND = Path(__file__).resolve().parent


def test_seasonal_plan_rbac_map() -> None:
    assert required_permission("GET", "/api/analytics/seasonal-plan") == "read"


def test_seasonal_plan_sqlite(tmp_path: Path) -> None:
    database = tmp_path / "seasonal-plan.db"
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
    assert "SEASONAL_PLAN_OK" in completed.stdout, completed.stdout + completed.stderr


SCENARIO = r'''
from datetime import date
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
        'current_password':'admin123','new_password':'SeasonalPlan123!',
    })
    assert changed.status_code == 200, changed.text
    headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

    company_a = client.post('/api/companies', headers=headers, json={'name':'Sezon Firma A'}).json()['id']
    company_b = client.post('/api/companies', headers=headers, json={'name':'Sezon Firma B'}).json()['id']
    year = date.today().year - 1

    def setup_company(company_id, prefix, stock, july_qty, august_qty):
        local_headers = dict(headers)
        local_headers['X-Company-ID'] = str(company_id)
        customer = client.post('/api/customers', headers=local_headers, json={'name':prefix+' Müşteri'}).json()['id']
        product = client.post('/api/products', headers=local_headers, json={
            'name':prefix+' Filtre','product_code':prefix+'-01','sale_price':'10',
            'stock':'200','unit':'Adet',
        }).json()['id']
        absent = client.post('/api/products', headers=local_headers, json={
            'name':prefix+' Satılmayan','product_code':prefix+'-00','sale_price':'10',
            'stock':'0','unit':'Adet',
        }).json()['id']
        def sale(month, qty, status='completed'):
            response = client.post('/api/orders', headers=local_headers, json={
                'entity_id':customer,'transaction_date':f'{year}-{month:02d}-10',
                'status':status,'payment_method':'cash','paid_amount':'0',
                'items':[{'product_id':product,'quantity':qty,'unit_price':'10','vat_rate':20}],
            })
            assert response.status_code == 201, response.text
            return response.json()['id']
        july_order = sale(7, july_qty)
        sale(8, august_qty)
        sale(9, '50', 'draft')
        with SessionLocal() as db:
            db.execute(text(
                'UPDATE products SET stock=:stock WHERE id=:id AND company_id=:cid'
            ), {'stock':stock,'id':product,'cid':company_id})
            db.commit()
        return local_headers, product, absent, july_order

    headers_a, product_a, absent_a, legacy_order = setup_company(company_a, 'A', '5.2500', '4.5000', '3.2500')
    headers_b, product_b, _, _ = setup_company(company_b, 'B', '0', '99', '1')

    # Clean installs enforce NOT NULL, while databases upgraded from early
    # releases may still contain NULL statuses. Reproduce that legacy shape in
    # both engines, then restore PostgreSQL's constraint in the finally block.
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
        unauthenticated = client.get('/api/analytics/seasonal-plan', params={'months':'7,8'})
        assert unauthenticated.status_code == 401, unauthenticated.text

        response = client.get('/api/analytics/seasonal-plan', headers=headers_a, params={'months':'8,7,8'})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['historical_year'] == year
        assert data['months'] == [7, 8]
        assert data['formula'] == 'max(historical_demand - current_stock, 0)'
        assert [row['product_id'] for row in data['items']] == [product_a]
        row = data['items'][0]
        # Includes July's legacy status=NULL order plus the normal August order.
        assert dec(row['historical_demand']) == Decimal('7.7500')
        assert dec(row['current_stock']) == Decimal('5.2500')
        assert dec(row['suggested_quantity']) == Decimal('2.5000')
        assert dec(data['total_historical_demand']) == Decimal('7.7500')
        assert dec(data['total_suggested_quantity']) == Decimal('2.5000')
        assert absent_a not in [item['product_id'] for item in data['items']]
        assert product_b not in [item['product_id'] for item in data['items']]

        july = client.get('/api/analytics/seasonal-plan', headers=headers_a, params={'months':'7'})
        assert july.status_code == 200, july.text
        assert dec(july.json()['items'][0]['historical_demand']) == Decimal('4.5000')
        assert dec(july.json()['items'][0]['suggested_quantity']) == Decimal('0')

        insights = client.get('/api/analytics/insights', headers=headers_a)
        assert insights.status_code == 200, insights.text
        profitable = {row['id']:row for row in insights.json()['profitable_products']}
        assert dec(profitable[product_a]['quantity']) == Decimal('7.7500')

        invalid = client.get('/api/analytics/seasonal-plan', headers=headers_a, params={'months':'0,13'})
        assert invalid.status_code == 422, invalid.text
    finally:
        if engine.dialect.name == 'postgresql':
            with engine.begin() as connection:
                connection.execute(text(
                    "UPDATE orders SET status='completed' WHERE id=:id AND company_id=:cid"
                ), {'id':legacy_order,'cid':company_a})
                if altered_pg_status:
                    connection.execute(text('ALTER TABLE orders ALTER COLUMN status SET NOT NULL'))

print('SEASONAL_PLAN_OK')
'''
