"""PostgreSQL ikizi: H122 — Vade farkı tahakkuk uçları yalnız charge_type='late_fee' belgelerine dokunur.

SQLite testi `tests/test_h122_late_fee_charge_type.py` aynı senaryoyu
(`tests/h122_senaryo.py`) geçici bir SQLite dosyasında koşturur.
Bu ikiz gerçek PostgreSQL motorunda:
1. bounced_check belgesi: /reversal, GET ve /post 404
2. service_fee belgesi: /reversal, GET ve /post 404
3. 404 sonrası aynı Idempotency-Key ile tekrar 404 (claim yanmaz)
4. Gerçek late_fee belgesi 200 ile terslenir, eksi tutarlı kayıt oluşur, replay 200 döner.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

from tests.pg_ikiz_yardimci import acilisa_cek, kosu_eki

pytestmark = pytest.mark.postgresql

KOSU = kosu_eki()


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("H122 ikizi APP_TEST_DATABASE_URL ister")
    return url


@pytest.fixture(autouse=True)
def _acilis_sifresi():
    _url()
    acilisa_cek()
    try:
        yield
    finally:
        acilisa_cek()


def test_h122_late_fee_charge_type_postgresql():
    url = _url()
    os.environ.setdefault("DATABASE_URL", url)
    from fastapi.testclient import TestClient

    from app.db import SessionLocal, engine
    from app.main import app
    from tests.h122_senaryo import oturum, run_h122_scenario, temizle_h122

    assert engine.dialect.name == "postgresql", engine.dialect.name
    acilisa_cek()

    with TestClient(app) as c:
        ctx = oturum(c, "H122TestPassPg123!")
        h = ctx["h"]
        cid = ctx["cid"]
        uid = ctx["uid"]

        with SessionLocal() as db:
            res = None
            try:
                res = run_h122_scenario(c, h, cid, uid, db, KOSU)
                assert res["bounced_doc_id"] > 0
                assert res["service_doc_id"] > 0
                assert res["late_fee_doc_id"] > 0
                assert res["reversal_doc_id"] > 0
            finally:
                if res is not None:
                    temizle_h122(db, cid, res)

    acilisa_cek()
