"""e-Fatura seam on real PostgreSQL 16.

Guards the dialect-sensitive parts of the seam migration + hook: the new
``einvoice_*`` columns, the JSON-as-TEXT (not JSONB) payload round-trip, the
``einvoice_status`` default and its index, and clean boolean/int handling.
Auto-discovered by the collapsed backend-postgresql job's ``test_*postgresql*.py``
glob (the 16th PG file).
"""

from __future__ import annotations

import json
import os
from decimal import Decimal

import pytest
from tests.pg_ikiz_yardimci import kosu_eki


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
def test_einvoice_seam_columns_and_payload_roundtrip_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    url = os.environ.get("EINVOICE_SEAM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("EINVOICE_SEAM_TEST_DATABASE_URL is not configured")
    monkeypatch.setenv("DATABASE_URL", url)

    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.main import app

    with TestClient(app) as c:
        login = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()
        cid = login["companies"][0]["id"]
        uid = login["user"]["id"]
        h = {"Authorization": "Bearer " + login["access_token"], "X-Company-ID": str(cid)}
        ch = c.post("/api/auth/change-password", headers=h,
                    json={"current_password": "admin123", "new_password": "EinvoiceSeamPg123!"}).json()
        h["Authorization"] = "Bearer " + ch["access_token"]

        cust = c.post("/api/customers", headers=h, json={"name": "PG Seam"}).json()
        mach = c.post("/api/machines", headers=h,
                      json={"customer_id": cust["id"], "brand": "PG", "model": "Seam", "serial_number": f"PG-SN-{kosu_eki()}"}).json()
        wo = c.post("/api/work-orders", headers=h,
                    json={"machine_id": mach["id"], "customer_id": cust["id"], "technician_id": uid,
                          "actual_hours": "2", "labor_rate": "50"}).json()
        for st in ("IN_PROGRESS", "COMPLETED"):
            assert c.patch(f"/api/work-orders/{wo['id']}/status", headers=h, json={"status": st}).status_code == 200

        inv = c.post("/api/invoices/generate", headers=h, json={"work_order_id": wo["id"]})
        assert inv.status_code == 201, inv.text
        detail = inv.json()
        iid = detail["id"]
        assert detail["einvoice_status"] == "NONE"
        grand = Decimal(str(detail["totals"]["grand_total"]))

    with SessionLocal() as db:
        row = db.execute(
            text("SELECT einvoice_status, einvoice_channel, einvoice_uuid, einvoice_payload "
                 "FROM invoices WHERE id=:id AND company_id=:cid"),
            {"id": iid, "cid": cid},
        ).mappings().one()
        # Defaulted/nullable columns behave on PG16.
        assert row["einvoice_status"] == "NONE"
        assert row["einvoice_channel"] is None and row["einvoice_uuid"] is None

        # JSON-as-TEXT round-trip: the payload is a parseable JSON string whose
        # totals match the invoice to the kuruş.
        payload = json.loads(row["einvoice_payload"])
        assert Decimal(payload["monetary_total"]["payable_amount"]) == grand

        # The payload column is TEXT, NOT JSONB (SQLite<->PG parity is deliberate).
        dtype = db.execute(
            text("SELECT data_type FROM information_schema.columns "
                 "WHERE table_name='invoices' AND column_name='einvoice_payload'")
        ).scalar_one()
        assert dtype == "text", dtype

        # einvoice_status carries the NONE default at the column level.
        default = db.execute(
            text("SELECT column_default FROM information_schema.columns "
                 "WHERE table_name='invoices' AND column_name='einvoice_status'")
        ).scalar_one()
        assert default is not None and "NONE" in default, default

        # The (company_id, einvoice_status) index exists.
        idx = db.execute(
            text("SELECT indexname FROM pg_indexes "
                 "WHERE tablename='invoices' AND indexname='ix_invoices_einvoice_status'")
        ).scalar()
        assert idx == "ix_invoices_einvoice_status"
