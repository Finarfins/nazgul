from __future__ import annotations

import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from app.money import money, quantity
from app.schemas import PaymentCreate, ProductCreate, TransactionCreate, TransactionItem

BACKEND = Path(__file__).resolve().parent


def test_decimal_helpers_use_accounting_rounding_without_float_drift() -> None:
    assert money("1.005") == Decimal("1.01")
    assert money(Decimal("0.1") + Decimal("0.2")) == Decimal("0.30")
    assert quantity("1.23456") == Decimal("1.2346")


def test_financial_request_models_keep_decimal_values() -> None:
    product = ProductCreate(name="Ondalık Ürün", purchase_price=0.1, sale_price="0.30", stock="1.2500")
    payment = PaymentCreate(
        entity_type="customer", entity_id=1, amount="0.10", payment_date="2026-07-13"
    )
    transaction = TransactionCreate(
        entity_id=1,
        transaction_date="2026-07-13",
        items=[
            TransactionItem(
                product_id=1,
                quantity="3.0000",
                unit_price="0.10",
                vat_rate=0,
                discount_percent="0",
            )
        ],
    )
    assert product.purchase_price == Decimal("0.1")
    assert product.stock == Decimal("1.2500")
    assert payment.amount == Decimal("0.10")
    assert transaction.items[0].unit_price == Decimal("0.10")


def test_api_totals_do_not_expose_binary_float_artifacts(tmp_path: Path) -> None:
    database = tmp_path / "decimal-api.db"
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
headers = {
    'Authorization': 'Bearer ' + body['access_token'],
    'X-Company-ID': str(body['companies'][0]['id']),
}
password_change = client.post('/api/auth/change-password', headers=headers, json={'current_password':'admin123','new_password':'AdminRot!2026x'})
assert password_change.status_code == 200, password_change.text
headers['Authorization'] = 'Bearer ' + password_change.json()['access_token']
customer = client.post('/api/customers', headers=headers, json={'name':'Decimal Müşteri'})
assert customer.status_code == 201, customer.text
product = client.post('/api/products', headers=headers, json={
    'name':'Decimal Ürün','purchase_price':'0.10','sale_price':'0.10',
    'vat_rate':0,'stock':'10.0000','unit':'Adet',
})
assert product.status_code == 201, product.text
warehouse = client.get('/api/warehouses', headers=headers).json()[0]
order = client.post('/api/orders', headers=headers, json={
    'entity_id':customer.json()['id'],'transaction_date':'2026-07-13',
    'warehouse_id':warehouse['id'],'status':'completed','payment_method':'cash',
    'paid_amount':'0.10','discount_percent':'0',
    'items':[{'product_id':product.json()['id'],'quantity':'3.0000',
              'unit_price':'0.10','vat_rate':0,'discount_percent':'0'}],
})
assert order.status_code == 201, order.text
payload = order.json()
assert Decimal(str(payload['final_total'])) == Decimal('0.30'), payload
assert Decimal(str(payload['paid_amount'])) == Decimal('0.10'), payload
assert Decimal(str(payload['remaining_amount'])) == Decimal('0.20'), payload
client.close()
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


def test_runtime_financial_code_does_not_use_binary_float_types() -> None:
    import ast

    offenders: list[str] = []
    app_dir = BACKEND / "app"
    for path in app_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "float":
                offenders.append(f"{path.relative_to(BACKEND)}:{node.lineno}: float")
            if isinstance(node, ast.Name) and node.id == "Float":
                offenders.append(f"{path.relative_to(BACKEND)}:{node.lineno}: Float")
    assert not offenders, "\n".join(offenders)


def test_excel_numeric_parser_preserves_decimal_text() -> None:
    from app.routers.imports import _num

    assert _num("1.234,56") == Decimal("1234.56")
    assert _num("1,234.56") == Decimal("1234.56")
    assert _num("0,10") == Decimal("0.10")
    assert _num(None, "20") == Decimal("20")
