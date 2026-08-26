from __future__ import annotations

import sqlite3
from pathlib import Path


def test_warehouse_transfer_and_stock_integrity(tmp_path: Path):
    import os

    database_path = tmp_path / "warehouse_transfer.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{database_path}"
    os.environ["AUTO_MIGRATE"] = "true"

    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin123"}
        )
        assert login.status_code == 200, login.text

        csrf = client.cookies.get("yhp_csrf_token")
        changed = client.post(
            "/api/auth/change-password",
            json={"current_password": "admin123", "new_password": "DemoPass123!"},
            headers={"X-CSRF-Token": csrf},
        )
        assert changed.status_code == 200, changed.text
        csrf = client.cookies.get("yhp_csrf_token")

        warehouses = client.get("/api/warehouses")
        assert warehouses.status_code == 200 and warehouses.json(), warehouses.text
        source_id = warehouses.json()[0]["id"]

        created = client.post(
            "/api/warehouses",
            json={"name": "Test İkinci Depo", "code": "T2"},
            headers={"X-CSRF-Token": csrf},
        )
        assert created.status_code == 201, created.text
        target_id = created.json()["id"]

        product = client.post(
            "/api/products",
            json={
                "name": "Depo Transfer Test Ürünü",
                "product_code": "WH-TRANSFER-1",
                "barcode": "8699999000001",
                "purchase_price": 100,
                "sale_price": 150,
                "vat_rate": 20,
                "stock": 0,
                "unit": "Adet",
                "category": "Test",
                "location": "A-01",
                "oem_number": "OEM-WH-001",
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert product.status_code == 201, product.text
        product_id = product.json()["id"]

        adjusted = client.post(
            f"/api/products/{product_id}/stock",
            json={
                "mode": "add",
                "warehouse_id": source_id,
                "quantity": 10,
                "movement_date": "2026-07-15",
                "note": "Depo transfer testi başlangıç stoğu",
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert adjusted.status_code == 200, adjusted.text

        transfer = client.post(
            "/api/warehouses/transfers",
            json={
                "source_warehouse_id": source_id,
                "target_warehouse_id": target_id,
                "transfer_date": "2026-07-15",
                "note": "Otomatik depo transfer testi",
                "items": [{"product_id": product_id, "quantity": 4}],
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert transfer.status_code == 201, transfer.text

        source_stock = client.get(
            "/api/warehouses/stock", params={"warehouse_id": source_id}
        )
        target_stock = client.get(
            "/api/warehouses/stock", params={"warehouse_id": target_id}
        )
        assert source_stock.status_code == 200, source_stock.text
        assert target_stock.status_code == 200, target_stock.text

        source_row = next(
            item for item in source_stock.json() if item["product_id"] == product_id
        )
        target_row = next(
            item for item in target_stock.json() if item["product_id"] == product_id
        )
        assert float(source_row["quantity"]) == 6
        assert float(target_row["quantity"]) == 4

        critical = client.post(
            "/api/warehouses/critical-stock",
            json={
                "product_id": product_id,
                "warehouse_id": target_id,
                "critical_stock": 5,
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert critical.status_code == 200, critical.text

        critical_rows = client.get(
            "/api/warehouses/stock",
            params={"warehouse_id": target_id, "critical_only": True},
        )
        assert critical_rows.status_code == 200, critical_rows.text
        assert any(
            item["product_id"] == product_id and item["is_critical"] == 1
            for item in critical_rows.json()
        )

        movements = client.get(
            "/api/products/stock/movements/all",
            params={"warehouse_id": target_id, "q": "Depo Transfer Test"},
        )
        assert movements.status_code == 200, movements.text
        assert any(
            item["product_id"] == product_id
            and item["movement_type"] == "transfer_in"
            for item in movements.json()
        )

    with sqlite3.connect(database_path) as connection:
        total_stock = connection.execute(
            "SELECT stock FROM products WHERE id = ?", (product_id,)
        ).fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]

    assert float(total_stock) == 10
    assert integrity == "ok"
