from __future__ import annotations

import os

import pytest


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
        from tests.pg_ikiz_yardimci import acilisa_cek
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
def test_machine_workorder_numeric_bounds_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    """PG twin of test_v2_9_machine_workorder_numeric_bounds.

    Proves parity: the column-maximum value is accepted and stored on real
    PostgreSQL, while over-max input is rejected with the same HTTP 422 as on
    SQLite -- i.e. the schema bound stops oversized values before they can
    overflow the NUMERIC columns (which would otherwise be a PG DataError/500).
    """
    database_url = os.environ.get("NUMERIC_BOUNDS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("NUMERIC_BOUNDS_TEST_DATABASE_URL is not configured")
    monkeypatch.setenv("DATABASE_URL", database_url)

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()
        cid = login["companies"][0]["id"]
        headers = {"Authorization": "Bearer " + login["access_token"], "X-Company-ID": str(cid)}
        changed = client.post("/api/auth/change-password", headers=headers, json={
            "current_password": "admin123", "new_password": "NumericBoundsPg123!"
        }).json()
        headers["Authorization"] = "Bearer " + changed["access_token"]

        def machine(**over):
            body = {"brand": "John Deere", "model": "6155R"}
            body.update(over)
            return client.post("/api/machines", headers=headers, json=body)

        # NUMERIC(12,2) column maximum is accepted and persists on real PG.
        ok = machine(working_hours="9999999999.99")
        assert ok.status_code == 201, ok.text
        fetched = client.get(f"/api/machines/{ok.json()['id']}", headers=headers).json()
        assert str(fetched["working_hours"]) in ("9999999999.99", "9999999999.9900"), fetched["working_hours"]

        # Over-max is rejected identically to SQLite (no NUMERIC overflow / 500).
        assert machine(working_hours="10000000000.00").status_code == 422

        def work_order(**over):
            body = {"machine_id": 1, "technician_id": 1}
            body.update(over)
            return client.post("/api/work-orders", headers=headers, json=body)

        assert work_order(labor_rate="100000001").status_code == 422
        assert work_order(actual_hours="1000001").status_code == 422
        assert work_order(estimated_hours="1000001").status_code == 422
