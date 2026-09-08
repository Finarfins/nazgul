from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier

import pytest

try:
    from tests.pg_ikiz_yardimci import kosu_eki
except ImportError:
    import uuid
    def kosu_eki() -> str:
        return uuid.uuid4().hex[:8]
def _acilisa_cek() -> None:
    """Admin şifresini AÇILIŞ DURUMUNA (`admin123` + `must_change_password`) yaz.

    D2/1B-A ikizlerinden DEVRALINDI. CI dosya başına `reset_schema` çalıştırdığı
    için (`ci.yml:609`) CI ortamında DB durumu paylaşılmaz; bu dikiş yerel/pglens
    paylaşılan veritabanı koşularını korur ve gelecekte reset_schema adımının
    kaldırılmasına karşı savunma sağlar. Tek yönlü bir çare (yalnız teardown)
    dosyayı iyi bir komşu yapar ama KENDİSİNİ korumaz, çünkü şifreyi bozan
    ÖNCEKİ dosya olabilir. Bu yüzden İKİ UÇTAN çağrılır.
    """
    try:
        from tests.pg_ikiz_yardimci import acilisa_cek, kosu_eki
        acilisa_cek()
    except ImportError:
        from sqlalchemy import text as _text
        from app.auth import hash_password
        from app.db import SessionLocal, engine as _eng

        if _eng.dialect.name != "postgresql":
            return
        with SessionLocal() as db:
            if db.execute(_text("SELECT to_regclass('public.app_users')")).scalar() is None:
                return
            db.execute(
                _text(
                    "UPDATE app_users SET password_hash=:h, "
                    "must_change_password=true WHERE username='admin'"
                ),
                {"h": hash_password("admin123")},
            )
            db.commit()


@pytest.fixture(autouse=True)
def _acilis_sifresi():
    _acilisa_cek()
    try:
        yield
    finally:
        _acilisa_cek()


@pytest.mark.postgresql
def test_immediate_parts_race_for_last_available_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.environ.get("WORK_ORDER_PARTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("WORK_ORDER_PARTS_TEST_DATABASE_URL is not configured")
    monkeypatch.setenv("DATABASE_URL", database_url)

    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.main import app

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin123"}
        ).json()
        cid = login["companies"][0]["id"]
        uid = login["user"]["id"]
        headers = {
            "Authorization": "Bearer " + login["access_token"],
            "X-Company-ID": str(cid),
        }
        changed = client.post(
            "/api/auth/change-password",
            headers=headers,
            json={
                "current_password": "admin123",
                "new_password": "ImmediateAvailability123!",
            },
        ).json()
        headers["Authorization"] = "Bearer " + changed["access_token"]
        customer = client.post(
            "/api/customers", headers=headers, json={"name": "Immediate Race"}
        ).json()
        k_ek = kosu_eki()
        machines = [
            client.post(
                "/api/machines",
                headers=headers,
                json={
                    "customer_id": customer["id"],
                    "brand": "Race",
                    "model": str(index),
                    "serial_number": f"IMMEDIATE-RACE-{index}-{k_ek}",
                },
            ).json()
            for index in range(2)
        ]

        warehouse = client.get("/api/warehouses", headers=headers).json()[0]
        product = client.post(
            "/api/products",
            headers=headers,
            json={
                "name": "Son Kullanılabilir Parça",
                "purchase_price": 5,
                "sale_price": 10,
                "stock": 1,
                "unit": "Adet",
            },
        ).json()

    barrier = Barrier(2)

    def create(machine_id: int) -> int:
        with TestClient(app) as concurrent:
            barrier.wait()
            response = concurrent.post(
                "/api/work-orders",
                headers=headers,
                json={
                    "machine_id": machine_id,
                    "customer_id": customer["id"],
                    "technician_id": uid,
                    "parts": [
                        {
                            "product_id": product["id"],
                            "warehouse_id": warehouse["id"],
                            "quantity": "1",
                            "unit_price": "5",
                        }
                    ],
                },
            )
            return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(create, [row["id"] for row in machines]))

    assert statuses == [201, 409]
    with SessionLocal() as db:
        stock = db.execute(
            text(
                """SELECT quantity,reserved_quantity FROM warehouse_stocks
                WHERE company_id=:cid AND warehouse_id=:wid AND product_id=:pid"""
            ),
            {"cid": cid, "wid": warehouse["id"], "pid": product["id"]},
        ).first()
        assert tuple(map(float, stock)) == (0, 0)
        assert (
            db.execute(
                text(
                    "SELECT COUNT(*) FROM work_order_parts "
                    "WHERE company_id=:cid AND product_id=:pid"
                ),
                {"cid": cid, "pid": product["id"]},
            ).scalar_one()
            == 1
        )
