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
from sqlalchemy import text
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
def test_parallel_completed_creates_one_service_receivable() -> None:
    database_url = os.getenv("APP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("APP_TEST_DATABASE_URL must point to PostgreSQL")

    from fastapi.testclient import TestClient
    from app.db import SessionLocal
    from app.main import app

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123"},
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
                "new_password": "ServiceReceivablePg123!",
            },
        ).json()
        headers["Authorization"] = "Bearer " + changed["access_token"]
        customer = client.post(
            "/api/customers", headers=headers, json={"name": "PG Service Cari"}
        ).json()
        machine = client.post(
            "/api/machines",
            headers=headers,
            json={
                "customer_id": customer["id"],
                "brand": "PG",
                "model": "Service",
                "serial_number": f"PG-SERVICE-REC-{kosu_eki()}",
            },
        ).json()
        work_order = client.post(
            "/api/work-orders",
            headers=headers,
            json={
                "machine_id": machine["id"],
                "customer_id": customer["id"],
                "technician_id": uid,
                "actual_hours": "1",
                "labor_rate": "100",
                "warranty_type": "NONE",
                "warranty_percent": "0",
            },
        ).json()
        work_order_id = int(work_order["id"])
        started = client.patch(
            f"/api/work-orders/{work_order_id}/status",
            headers=headers,
            json={"status": "IN_PROGRESS"},
        )
        assert started.status_code == 200, started.text

    barrier = Barrier(2)

    def complete() -> int:
        with TestClient(app) as concurrent_client:
            barrier.wait()
            response = concurrent_client.patch(
                f"/api/work-orders/{work_order_id}/status",
                headers=headers,
                json={"status": "COMPLETED"},
            )
            return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(complete) for _ in range(2)]
        statuses = sorted(future.result() for future in futures)

    assert statuses == [200, 409]
    with SessionLocal() as db:
        count = db.execute(
            text(
                """SELECT COUNT(*) FROM receivable_charge_documents
                WHERE company_id=:cid AND work_order_id=:work_order_id
                  AND charge_type='service_fee'"""
            ),
            {"cid": cid, "work_order_id": work_order_id},
        ).scalar_one()
        assert count == 1
