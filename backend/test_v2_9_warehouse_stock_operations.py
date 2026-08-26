from __future__ import annotations

import os
from pathlib import Path


def test_warehouse_stock_filter_and_adjustment(tmp_path: Path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'warehouse_stock.db'}"
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
        warehouse_id = warehouses.json()[0]["id"]

        product = client.post(
            "/api/products",
            json={
                "name": "CS640 Kritik Kayış",
                "product_code": "STK-CRIT-1",
                "barcode": "8691111000001",
                "purchase_price": 100,
                "sale_price": 150,
                "vat_rate": 20,
                "stock": 8,
                "unit": "Adet",
                "category": "Kayış",
                "location": "A-12",
                "oem_number": "84977489",
                "brand": "Rubena",
                "compatible_models": "CS640",
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert product.status_code == 201, product.text
        product_id = product.json()["id"]

        critical = client.post(
            "/api/warehouses/critical-stock",
            json={
                "product_id": product_id,
                "warehouse_id": warehouse_id,
                "critical_stock": 10,
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert critical.status_code == 200, critical.text

        critical_rows = client.get(
            "/api/warehouses/stock",
            params={"warehouse_id": warehouse_id, "critical_only": True},
        )
        assert critical_rows.status_code == 200, critical_rows.text
        row = next(x for x in critical_rows.json() if x["product_id"] == product_id)
        assert row["is_critical"] == 1
        assert row["oem_number"] == "84977489"
        assert row["location"] == "A-12"

        by_oem = client.get(
            "/api/warehouses/stock",
            params={"warehouse_id": warehouse_id, "q": "84977489"},
        )
        assert any(x["product_id"] == product_id for x in by_oem.json())

        adjusted = client.post(
            f"/api/products/{product_id}/stock",
            json={
                "mode": "set",
                "warehouse_id": warehouse_id,
                "quantity": 15,
                "movement_date": "2026-07-15",
                "note": "Fiziksel depo sayımı",
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert adjusted.status_code == 200, adjusted.text
        assert float(adjusted.json()["stock"]) == 15

        no_longer_critical = client.get(
            "/api/warehouses/stock",
            params={"warehouse_id": warehouse_id, "critical_only": True},
        )
        assert all(x["product_id"] != product_id for x in no_longer_critical.json())

        movements = client.get(
            "/api/products/stock/movements/all",
            params={"warehouse_id": warehouse_id, "q": "CS640 Kritik"},
        )
        assert movements.status_code == 200, movements.text
        assert any(
            x["product_id"] == product_id
            and x["movement_type"] == "set"
            and x["note"] == "Fiziksel depo sayımı"
            for x in movements.json()
        )
