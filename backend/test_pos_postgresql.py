from __future__ import annotations

import os
from decimal import Decimal

import pytest

try:
    from tests.pg_ikiz_yardimci import kosu_eki
except ImportError:
    import uuid
    def kosu_eki() -> str:
        return uuid.uuid4().hex[:8]
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

        k_ek = kosu_eki()
        pos_barcode = f"8690{k_ek}"
        product = client.post(
            "/api/products",
            headers=headers,
            json={
                "name": "PG POS Filtre",
                "product_code": f"PG-POS-{k_ek}",
                "barcode": pos_barcode,
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
            params={"barcode": pos_barcode},
        )
        assert lookup.status_code == 200, lookup.text
        assert lookup.json()["id"] == product_id

        sale = client.post(
            "/api/pos/sale",
            headers={**headers, "Idempotency-Key": f"pg-pos-sale-{k_ek}"},
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
            headers={**headers, "Idempotency-Key": f"pg-pos-insufficient-{k_ek}"},
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
                    "SELECT SUM(quantity) FROM warehouse_stocks "
                    "WHERE company_id=:cid AND product_id=:pid"
                ),
                {"cid": company_id, "pid": product_id},
            ).scalar_one()
            assert Decimal(stock) == Decimal("1")
