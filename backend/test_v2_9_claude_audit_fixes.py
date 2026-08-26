from __future__ import annotations

import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.orm import Session

from app.core_schema import products
from app.inventory import sync_product_stock, warehouse_stocks
from app.money import apply_global_discount
from app.routers.imports import _num

BACKEND = Path(__file__).resolve().parent


def test_global_discount_reduces_tax_base_and_preserves_invariant() -> None:
    subtotal, vat, discount, final = apply_global_discount(
        Decimal("100.00"), Decimal("20.00"), Decimal("120.00"), Decimal("10")
    )
    assert subtotal == Decimal("90.00")
    assert vat == Decimal("18.00")
    assert discount == Decimal("12.00")
    assert final == Decimal("108.00")
    assert subtotal + vat == final


def test_locale_numeric_parser_is_strict_and_supports_tr_and_us_formats() -> None:
    assert _num("1.234,56") == Decimal("1234.56")
    assert _num("1,234.56") == Decimal("1234.56")
    assert _num("0,10") == Decimal("0.10")
    with pytest.raises(ValueError):
        _num("1,234")
    with pytest.raises(ValueError):
        _num("abc")
    with pytest.raises(ValueError):
        _num("12,34,5")


def test_product_stock_summary_is_updated_with_one_atomic_statement(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'stock-summary.db').as_posix()}")
    products.create(engine)
    warehouse_stocks.create(engine)
    with engine.begin() as conn:
        product_id = int(
            conn.execute(
                insert(products)
                .values(
                    name="Atomic Summary",
                    purchase_price=Decimal("1.00"),
                    sale_price=Decimal("2.00"),
                    vat_rate=20,
                    stock=Decimal("0.0000"),
                    unit="Adet",
                    company_id=1,
                )
                .returning(products.c.id)
            ).scalar_one()
        )
        conn.execute(
            insert(warehouse_stocks),
            [
                {"warehouse_id": 1, "product_id": product_id, "quantity": Decimal("5.0000"), "company_id": 1, "critical_stock": 0},
                {"warehouse_id": 2, "product_id": product_id, "quantity": Decimal("7.0000"), "company_id": 1, "critical_stock": 0},
            ],
        )

    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    with Session(engine) as db:
        total = sync_product_stock(db, 1, product_id)
        db.commit()
        stored = db.execute(select(products.c.stock).where(products.c.id == product_id)).scalar_one()

    assert total == Decimal("12.0000")
    assert Decimal(str(stored)) == Decimal("12.0000")
    summary_updates = [sql for sql in statements if sql.lstrip().upper().startswith("UPDATE PRODUCTS")]
    assert len(summary_updates) == 1
    assert "SELECT" in summary_updates[0].upper() and "SUM" in summary_updates[0].upper()


def test_password_gate_tax_totals_and_role_hierarchy_on_clean_database(tmp_path: Path) -> None:
    database = tmp_path / "claude-audit-fixes.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    script = r'''
from decimal import Decimal
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
body = login.json()
company_id = body['companies'][0]['id']
headers = {'Authorization':'Bearer ' + body['access_token'], 'X-Company-ID':str(company_id)}

blocked = client.get('/api/customers', headers=headers)
assert blocked.status_code == 403, blocked.text
assert blocked.json().get('code') == 'PASSWORD_CHANGE_REQUIRED', blocked.text
changed = client.post('/api/auth/change-password', headers=headers, json={'current_password':'admin123','new_password':'AdminRot!2026x'})
assert changed.status_code == 200, changed.text
headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

customer = client.post('/api/customers', headers=headers, json={'name':'KDV Müşterisi'})
assert customer.status_code == 201, customer.text
product = client.post('/api/products', headers=headers, json={
    'name':'KDV Ürünü','purchase_price':'50.00','sale_price':'120.00',
    'vat_rate':20,'stock':'10.0000','unit':'Adet',
})
assert product.status_code == 201, product.text
warehouse = client.get('/api/warehouses', headers=headers).json()[0]
payload = {
    'entity_id':customer.json()['id'],'transaction_date':'2026-07-13',
    'warehouse_id':warehouse['id'],'status':'completed','payment_method':'credit',
    'paid_amount':'0.00','discount_percent':'10',
    'items':[{'product_id':product.json()['id'],'quantity':'1.0000','unit_price':'120.00','vat_rate':20,'discount_percent':'0'}],
}
order = client.post('/api/orders', headers=headers, json=payload)
assert order.status_code == 201, order.text
order_body = order.json()
assert Decimal(str(order_body['subtotal'])) == Decimal('90.00'), order_body
assert Decimal(str(order_body['vat_total'])) == Decimal('18.00'), order_body
assert Decimal(str(order_body['final_total'])) == Decimal('108.00'), order_body
assert Decimal(str(order_body['subtotal'])) + Decimal(str(order_body['vat_total'])) == Decimal(str(order_body['final_total']))
detail = client.get(f"/api/orders/{order_body['id']}", headers=headers)
assert detail.status_code == 200, detail.text
stored = detail.json()['document']
assert Decimal(str(stored['subtotal'])) == Decimal('90.00'), stored
assert Decimal(str(stored['vat_total'])) == Decimal('18.00'), stored
assert Decimal(str(stored['final_total'])) == Decimal('108.00'), stored

quote = client.post('/api/workflow/quote', headers=headers, json={
    'entity_id':customer.json()['id'],'document_date':'2026-07-13',
    'warehouse_id':warehouse['id'],'status':'approved','discount_percent':'10',
    'items':[{'product_id':product.json()['id'],'quantity':'1.0000','unit_price':'120.00','vat_rate':20,'discount_percent':'0'}],
})
assert quote.status_code == 200, quote.text
quote_detail = client.get(f"/api/workflow/quote/{quote.json()['id']}", headers=headers)
assert quote_detail.status_code == 200, quote_detail.text
quote_doc = quote_detail.json()['document']
assert Decimal(str(quote_doc['subtotal'])) == Decimal('90.00'), quote_doc
assert Decimal(str(quote_doc['vat_total'])) == Decimal('18.00'), quote_doc
assert Decimal(str(quote_doc['grand_total'])) == Decimal('108.00'), quote_doc

manager = client.post('/api/users', headers=headers, json={
    'username':'manager_audit','display_name':'Manager Audit','password':'ManagerInit!23','role':'yonetici',
})
assert manager.status_code == 201, manager.text
manager_login = client.post('/api/auth/login', json={'username':'manager_audit','password':'ManagerInit!23'})
assert manager_login.status_code == 200, manager_login.text
manager_headers = {'Authorization':'Bearer ' + manager_login.json()['access_token'], 'X-Company-ID':str(company_id)}
# Admin-created manager must clear the forced initial rotation before its role
# permissions are evaluated on protected calls.
manager_rotate = client.post('/api/auth/change-password', headers=manager_headers, json={'current_password':'ManagerInit!23','new_password':'ManagerRot!2026x'})
assert manager_rotate.status_code == 200, manager_rotate.text
manager_headers['Authorization'] = 'Bearer ' + manager_rotate.json()['access_token']
peer = client.post('/api/users', headers=manager_headers, json={
    'username':'manager_peer','display_name':'Manager Peer','password':'PeerInit!2345','role':'yonetici',
})
assert peer.status_code == 403, peer.text
disable_admin = client.patch(f"/api/users/{body['user']['id']}/status", headers=manager_headers, params={'is_active':'false'})
assert disable_admin.status_code == 403, disable_admin.text
reporter = client.post('/api/users', headers=manager_headers, json={
    'username':'reporter_ok','display_name':'Reporter OK','password':'ReporterInit!23','role':'rapor',
})
assert reporter.status_code == 201, reporter.text
reporter_login = client.post('/api/auth/login', json={'username':'reporter_ok','password':'ReporterInit!23'})
assert reporter_login.status_code == 200, reporter_login.text
reporter_headers = {'Authorization':'Bearer ' + reporter_login.json()['access_token'], 'X-Company-ID':str(company_id)}
# Reporter likewise clears the forced rotation so the following denials are true
# role-permission (rapor) rejections, not the password-change gate.
reporter_rotate = client.post('/api/auth/change-password', headers=reporter_headers, json={'current_password':'ReporterInit!23','new_password':'ReporterRot!2026x'})
assert reporter_rotate.status_code == 200, reporter_rotate.text
reporter_headers['Authorization'] = 'Bearer ' + reporter_rotate.json()['access_token']
assert client.get('/api/users', headers=reporter_headers).status_code == 403
assert client.get('/api/audit', headers=reporter_headers).status_code == 403
assert client.get('/api/finance/accounts', headers=reporter_headers).status_code == 403
client.close()
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=150,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
