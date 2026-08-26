"""Real PostgreSQL concurrency coverage for POS idempotency.

The tests force two requests into the critical sections together. Tenant-scoped
primary keys must roll back the losing transaction before replaying the winning
sale, and must keep exactly one walk-in system customer per tenant.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest


@pytest.mark.postgresql
def test_pos_concurrency_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = os.environ.get("POS_TEST_DATABASE_URL") or os.environ.get(
        "APP_TEST_DATABASE_URL"
    )
    if not database_url:
        pytest.skip("POS_TEST_DATABASE_URL or APP_TEST_DATABASE_URL is required")
    assert database_url.startswith("postgresql")
    monkeypatch.setenv("DATABASE_URL", database_url)

    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.main import app
    from app.routers import pos as pos_router

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin123"}
        )
        assert login.status_code == 200, login.text
        auth = login.json()
        headers = {
            "Authorization": "Bearer " + auth["access_token"],
            "X-Company-ID": str(auth["companies"][0]["id"]),
        }
        changed = client.post(
            "/api/auth/change-password",
            headers=headers,
            json={
                "current_password": "admin123",
                "new_password": "PosIdempotency123!",
            },
        )
        assert changed.status_code == 200, changed.text
        headers["Authorization"] = "Bearer " + changed.json()["access_token"]

        company = client.post(
            "/api/companies", headers=headers, json={"name": "POS Race Firma"}
        )
        assert company.status_code == 201, company.text
        company_id = company.json()["id"]
        headers["X-Company-ID"] = str(company_id)

        product = client.post(
            "/api/products",
            headers=headers,
            json={
                "name": "POS Race Filtre",
                "product_code": "POS-RACE-1",
                "barcode": "869000009999",
                "purchase_price": "10.00",
                "sale_price": "25.00",
                "vat_rate": 20,
                "stock": "10",
                "unit": "Adet",
            },
        )
        assert product.status_code == 201, product.text
        product_id = product.json()["id"]
        customer = client.post(
            "/api/customers", headers=headers, json={"name": "POS Race Müşteri"}
        )
        assert customer.status_code == 201, customer.text
        customer_id = customer.json()["id"]

        # Force both requests past the replay pre-check before either starts the
        # sales transaction. The PK conflict must roll back the losing order,
        # stock movement, payment and audit writes.
        payload = {
            "items": [
                {"product_id": product_id, "quantity": "1", "unit_price": "25.00"}
            ],
            "payment_type": "cash",
            "customer_id": customer_id,
        }
        race_headers = {**headers, "Idempotency-Key": "pos-concurrent-race-1"}
        original_save = pos_router._save
        save_gate = Barrier(2)

        def synchronized_save(*args, **kwargs):
            save_gate.wait(timeout=15)
            return original_save(*args, **kwargs)

        monkeypatch.setattr(pos_router, "_save", synchronized_save)

        def submit_named_sale(_index: int):
            return client.post("/api/pos/sale", headers=race_headers, json=payload)

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(submit_named_sale, range(2)))

        assert [response.status_code for response in responses] == [201, 201], [
            response.text for response in responses
        ]
        sale_ids = {response.json()["sale_id"] for response in responses}
        assert len(sale_ids) == 1
        sale_id = sale_ids.pop()

        with SessionLocal() as db:
            assert db.execute(
                text("SELECT COUNT(*) FROM orders WHERE company_id=:cid"),
                {"cid": company_id},
            ).scalar_one() == 1
            assert db.execute(
                text(
                    "SELECT COUNT(*) FROM pos_idempotency "
                    "WHERE company_id=:cid AND order_id=:order_id"
                ),
                {"cid": company_id, "order_id": sale_id},
            ).scalar_one() == 1
            stock = db.execute(
                text(
                    "SELECT quantity FROM warehouse_stocks "
                    "WHERE company_id=:cid AND product_id=:product_id"
                ),
                {"cid": company_id, "product_id": product_id},
            ).scalar_one()
            assert Decimal(str(stock)) == Decimal("9")

        changed_payload = {**payload, "note": "different request"}
        conflict = client.post(
            "/api/pos/sale", headers=race_headers, json=changed_payload
        )
        assert conflict.status_code == 409, conflict.text

        # Now force two first walk-in requests to observe no mapping together.
        # Only one provisional customer may survive the tenant PK race.
        monkeypatch.setattr(pos_router, "_save", original_save)
        original_mapped = pos_router._mapped_retail_customer_id
        mapping_gate = Barrier(2)

        def synchronized_mapping(db, cid):
            result = original_mapped(db, cid)
            if result is None:
                mapping_gate.wait(timeout=15)
            return result

        monkeypatch.setattr(
            pos_router, "_mapped_retail_customer_id", synchronized_mapping
        )
        walk_in_payload = {
            "items": [
                {"product_id": product_id, "quantity": "1", "unit_price": "25.00"}
            ],
            "payment_type": "cash",
        }

        def submit_walk_in(index: int):
            walk_in_headers = {
                **headers,
                "Idempotency-Key": f"pos-system-customer-race-{index}",
            }
            return client.post(
                "/api/pos/sale", headers=walk_in_headers, json=walk_in_payload
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            walk_in_responses = list(pool.map(submit_walk_in, range(2)))

        assert [response.status_code for response in walk_in_responses] == [201, 201], [
            response.text for response in walk_in_responses
        ]
        assert len({response.json()["sale_id"] for response in walk_in_responses}) == 2

        with SessionLocal() as db:
            assert db.execute(
                text("SELECT COUNT(*) FROM orders WHERE company_id=:cid"),
                {"cid": company_id},
            ).scalar_one() == 3
            assert db.execute(
                text("SELECT COUNT(*) FROM pos_idempotency WHERE company_id=:cid"),
                {"cid": company_id},
            ).scalar_one() == 3
            assert db.execute(
                text("SELECT COUNT(*) FROM pos_system_customers WHERE company_id=:cid"),
                {"cid": company_id},
            ).scalar_one() == 1
            assert db.execute(
                text(
                    "SELECT COUNT(*) FROM customers "
                    "WHERE company_id=:cid AND name='Perakende Satış'"
                ),
                {"cid": company_id},
            ).scalar_one() == 1
            stock = db.execute(
                text(
                    "SELECT quantity FROM warehouse_stocks "
                    "WHERE company_id=:cid AND product_id=:product_id"
                ),
                {"cid": company_id, "product_id": product_id},
            ).scalar_one()
            assert Decimal(str(stock)) == Decimal("7")
