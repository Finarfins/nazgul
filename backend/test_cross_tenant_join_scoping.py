from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parent


def test_cross_tenant_left_joins_do_not_leak_other_company_fields(tmp_path: Path) -> None:
    """Cross-tenant JOINs must be scoped to the requesting company.

    Checks all 6 cross-tenant join surfaces:
    1. `analytics.insights` (profitable products): `LEFT JOIN products p`
    2. `finance.list_instruments`: `LEFT JOIN customers c`, `suppliers s`, `finance_accounts a`
    3. `search`: order search (`JOIN customers c`)
    4. `search`: purchase search (`JOIN suppliers s`)
    5. `transactions.detail`: order/purchase detail (`JOIN customer/supplier`, `LEFT JOIN warehouses w`)
    6. `finance.list_finance_transactions`: `JOIN finance_accounts a`

    Plant tenant-B rows, point tenant-A rows at those B IDs (the collision/stale-FK case
    via direct Core inserts), then assert none of B's fields surface in A's responses.
    """
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'cross_tenant.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy import insert
from app.db import SessionLocal
from app.core_schema import products, customers, suppliers, orders, order_items, purchases, purchase_items
from app.finance_engine import finance_accounts, finance_transactions, financial_instruments
from app.inventory import warehouses
from app.main import app

LEAK_PRODUCT = "LEAK_PRODUCT_B"
LEAK_CUSTOMER = "LEAK_CUSTOMER_B"
LEAK_SUPPLIER = "LEAK_SUPPLIER_B"
LEAK_ACCOUNT = "LEAK_ACCOUNT_B"
LEAK_WAREHOUSE = "LEAK_WAREHOUSE_B"

with TestClient(app) as c:
    login = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()
    cid_a = login["companies"][0]["id"]
    h = {"Authorization": "Bearer " + login["access_token"], "X-Company-ID": str(cid_a)}
    changed = c.post("/api/auth/change-password", headers=h,
                     json={"current_password": "admin123", "new_password": "CrossTenantJoin123!"}).json()
    h["Authorization"] = "Bearer " + changed["access_token"]

    # Second tenant (B) owns the rows that must never leak into A.
    cid_b = c.post("/api/companies", headers=h, json={"name": "Cross Tenant B"}).json()["id"]
    assert cid_b != cid_a

    # Real entities in A for valid baseline references.
    cust_a = c.post("/api/customers", headers=h, json={"name": "Tenant A Customer"}).json()["id"]
    sup_a = c.post("/api/suppliers", headers=h, json={"name": "Tenant A Supplier"}).json()["id"]

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        pid_b = db.execute(insert(products).values(
            name=LEAK_PRODUCT, purchase_price=999, sale_price=999, company_id=cid_b)).inserted_primary_key[0]
        cust_b = db.execute(insert(customers).values(
            name=LEAK_CUSTOMER, company_id=cid_b)).inserted_primary_key[0]
        sup_b = db.execute(insert(suppliers).values(
            name=LEAK_SUPPLIER, company_id=cid_b)).inserted_primary_key[0]
        acc_b = db.execute(insert(finance_accounts).values(
            name=LEAK_ACCOUNT, account_type="bank", currency="TRY", company_id=cid_b,
            created_at=now)).inserted_primary_key[0]
        wh_b = db.execute(insert(warehouses).values(
            name=LEAK_WAREHOUSE, code="WHB", company_id=cid_b)).inserted_primary_key[0]

        # Order A pointing at valid customer A, but tenant B warehouse (wh_b).
        ord_a = db.execute(insert(orders).values(
            customer_id=cust_a, order_date="2026-01-01", status="completed", document_no="ORD-A-001",
            warehouse_id=wh_b, final_total=100, company_id=cid_a)).inserted_primary_key[0]
        db.execute(insert(order_items).values(
            company_id=cid_a,
            order_id=ord_a, product_id=pid_b, product_name="planted", quantity=1,
            unit_price=10, line_total=10))

        # Order A invalid: customer_id points to tenant B customer (cust_b).
        ord_a_invalid = db.execute(insert(orders).values(
            customer_id=cust_b, order_date="2026-01-01", status="completed", document_no="ORD-A-002",
            warehouse_id=None, final_total=100, company_id=cid_a)).inserted_primary_key[0]

        # Purchase A pointing at valid supplier A, but tenant B warehouse (wh_b).
        pur_a = db.execute(insert(purchases).values(
            supplier_id=sup_a, purchase_date="2026-01-01", status="completed", document_no="PUR-A-001",
            warehouse_id=wh_b, final_total=50, company_id=cid_a)).inserted_primary_key[0]
        db.execute(insert(purchase_items).values(
            company_id=cid_a,
            purchase_id=pur_a, product_id=pid_b, product_name="planted", quantity=1,
            unit_price=10, line_total=10))

        # Purchase A invalid: supplier_id points to tenant B supplier (sup_b).
        pur_a_invalid = db.execute(insert(purchases).values(
            supplier_id=sup_b, purchase_date="2026-01-01", status="completed", document_no="PUR-A-002",
            warehouse_id=None, final_total=50, company_id=cid_a)).inserted_primary_key[0]

        db.execute(insert(financial_instruments).values(
            company_id=cid_a, instrument_type="check", direction="received", status="portfolio",
            entity_type="customer", entity_id=cust_b, account_id=acc_b, amount=100,
            due_date="2026-02-01", created_at=now))
        db.execute(insert(financial_instruments).values(
            company_id=cid_a, instrument_type="promissory_note", direction="issued", status="portfolio",
            entity_type="supplier", entity_id=sup_b, amount=50,
            due_date="2026-03-01", created_at=now))
        cross_account_tx = db.execute(insert(finance_transactions).values(
            company_id=cid_a, account_id=acc_b, txn_date="2026-01-02", direction="in",
            amount=25, category="planted", description="cross-account", created_at=now,
        )).inserted_primary_key[0]
        db.commit()
    finally:
        db.close()

    # 1. analytics: profitable products must not expose tenant B's product
    insights = c.get("/api/analytics/insights", headers=h)
    assert insights.status_code == 200, insights.text
    profitable = insights.json().get("profitable_products", [])
    assert all(row.get("name") != LEAK_PRODUCT for row in profitable), ("product leak", profitable)
    assert LEAK_PRODUCT not in insights.text, ("product leak in body", insights.text)

    # 2. finance: instrument entity/account names must not expose tenant B's rows
    instruments = c.get("/api/finance/instruments", headers=h)
    assert instruments.status_code == 200, instruments.text
    rows = instruments.json()
    assert len(rows) == 2, rows
    for leak in (LEAK_CUSTOMER, LEAK_SUPPLIER, LEAK_ACCOUNT):
        assert leak not in instruments.text, ("finance leak", leak, instruments.text)
    for row in rows:
        assert row.get("entity_name") in (None, ""), ("entity_name leak", row)
        assert row.get("account_name") in (None, ""), ("account_name leak", row)

    # 3. finance transactions: tenant-A row with tenant-B account must not join.
    finance_transactions_response = c.get("/api/finance/transactions", headers=h)
    assert finance_transactions_response.status_code == 200, finance_transactions_response.text
    finance_rows = finance_transactions_response.json()
    assert all(row.get("id") != cross_account_tx for row in finance_rows), finance_rows
    assert LEAK_ACCOUNT not in finance_transactions_response.text, finance_transactions_response.text

    # 4. search: order search matching LEAK_CUSTOMER must return zero results
    search_cust = c.get(f"/api/search?q={LEAK_CUSTOMER}", headers=h)
    assert search_cust.status_code == 200, search_cust.text
    sales_found = [item for item in search_cust.json().get("results", []) if item.get("type") == "sale"]
    assert len(sales_found) == 0, ("search customer leak", sales_found)

    # 5. search: purchase search matching LEAK_SUPPLIER must return zero results
    search_sup = c.get(f"/api/search?q={LEAK_SUPPLIER}", headers=h)
    assert search_sup.status_code == 200, search_sup.text
    purchases_found = [item for item in search_sup.json().get("results", []) if item.get("type") == "purchase"]
    assert len(purchases_found) == 0, ("search supplier leak", purchases_found)

    # 6. transactions detail:
    # a) valid entity but tenant-B warehouse -> warehouse_name must be None
    ord_detail = c.get(f"/api/orders/{ord_a}", headers=h)
    assert ord_detail.status_code == 200, ord_detail.text
    assert ord_detail.json()["document"]["customer_name"] == "Tenant A Customer"
    assert ord_detail.json()["document"]["warehouse_name"] is None, ("transaction order warehouse leak", ord_detail.json())

    pur_detail = c.get(f"/api/purchases/{pur_a}", headers=h)
    assert pur_detail.status_code == 200, pur_detail.text
    assert pur_detail.json()["document"]["supplier_name"] == "Tenant A Supplier"
    assert pur_detail.json()["document"]["warehouse_name"] is None, ("transaction purchase warehouse leak", pur_detail.json())

    # b) cross-tenant entity ID on order/purchase -> inner join fails, returning 404
    assert c.get(f"/api/orders/{ord_a_invalid}", headers=h).status_code == 404
    assert c.get(f"/api/purchases/{pur_a_invalid}", headers=h).status_code == 404

print("CROSS_TENANT_JOIN_SCOPING_OK")
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke], cwd=BACKEND, env=env, text=True, capture_output=True, timeout=180
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "CROSS_TENANT_JOIN_SCOPING_OK" in result.stdout, result.stdout + "\n" + result.stderr
