from __future__ import annotations

import os
from decimal import Decimal

import pytest
from sqlalchemy import text


def _postgres_url() -> str:
    database_url = os.environ.get("POS_TEST_DATABASE_URL") or os.environ.get(
        "APP_TEST_DATABASE_URL"
    )
    if not database_url:
        pytest.skip("POS_TEST_DATABASE_URL or APP_TEST_DATABASE_URL is required")
    # Guard the point of the twin: a misconfigured URL must fail loudly instead
    # of silently re-running the scenario on SQLite.
    assert database_url.startswith("postgresql"), (
        "the PostgreSQL twin must not run on another engine: " + database_url
    )
    return database_url


# NOTE: the CI PG lane resets the schema once per FILE, not per test, and the
# admin bootstrap password can only be rotated once. A second scenario that logs
# in as admin therefore belongs in its own file — see
# test_pos_customer_postgresql.py — otherwise whichever test runs second gets a
# 401 from the password the first one rotated.
@pytest.mark.postgresql
def test_pos_core_path_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", _postgres_url())

    from fastapi.testclient import TestClient
    from app.db import SessionLocal
    from app.main import app

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin123"}
        )
        assert login.status_code == 200, login.text
        body = login.json()
        company_id = body["companies"][0]["id"]
        headers = {
            "Authorization": "Bearer " + body["access_token"],
            "X-Company-ID": str(company_id),
        }
        changed = client.post(
            "/api/auth/change-password",
            headers=headers,
            json={
                "current_password": "admin123",
                "new_password": "PosPostgresql123!",
            },
        )
        assert changed.status_code == 200, changed.text
        headers["Authorization"] = "Bearer " + changed.json()["access_token"]

        product = client.post(
            "/api/products",
            headers=headers,
            json={
                "name": "PG POS Filtre",
                "product_code": "PG-POS-1",
                "barcode": "869000009999",
                "purchase_price": "5.00",
                "sale_price": "19.95",
                "vat_rate": 20,
                "stock": "3",
                "unit": "Adet",
            },
        )
        assert product.status_code == 201, product.text
        product_id = product.json()["id"]

        lookup = client.get(
            "/api/pos/lookup",
            headers=headers,
            params={"barcode": "869000009999"},
        )
        assert lookup.status_code == 200, lookup.text
        assert lookup.json()["id"] == product_id

        sale = client.post(
            "/api/pos/sale",
            headers={**headers, "Idempotency-Key": "pg-pos-sale"},
            json={
                "items": [
                    {
                        "product_id": product_id,
                        "quantity": "2",
                        "unit_price": "19.95",
                    }
                ],
                "payment_type": "card",
            },
        )
        assert sale.status_code == 201, sale.text
        assert Decimal(str(sale.json()["final_total"])) == Decimal("39.90")

        blocked = client.post(
            "/api/pos/sale",
            headers={**headers, "Idempotency-Key": "pg-pos-insufficient"},
            json={
                "items": [
                    {
                        "product_id": product_id,
                        "quantity": "2",
                        "unit_price": "19.95",
                    }
                ],
                "payment_type": "card",
            },
        )
        assert blocked.status_code == 409, blocked.text

        with SessionLocal() as db:
            stock = db.execute(
                text(
                    "SELECT quantity FROM warehouse_stocks "
                    "WHERE company_id=:cid AND product_id=:pid"
                ),
                {"cid": company_id, "pid": product_id},
            ).scalar_one()
            assert Decimal(stock) == Decimal("1")
