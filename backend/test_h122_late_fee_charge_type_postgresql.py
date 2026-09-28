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
    from typing import Any
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
            res: dict[str, Any] = {}
            try:
                run_h122_scenario(c, h, cid, uid, db, KOSU, res=res)
                assert res["bounced_doc_id"] > 0
                assert res["service_doc_id"] > 0
                assert res["late_fee_doc_id"] > 0
                assert res["reversal_doc_id"] > 0
            finally:
                temizle_h122(db, cid, res, kosu=KOSU)

    acilisa_cek()


def test_h122_concurrent_same_key_reversal_postgresql():
    """H122: İki eşzamanlı istek aynı Idempotency-Key ile aynı vade farkı belgesini tersler.

    Her iki istek de 200 dönmeli, ikisi de aynı ters kayıt id'sini almalı ve
    veritabanında tam olarak BİR adet eksi tutarlı ters kayıt satırı oluşmalıdır.
    """
    url = _url()
    os.environ.setdefault("DATABASE_URL", url)
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from typing import Any
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from app.db import SessionLocal, engine
    from app.main import app
    from tests.h122_senaryo import oturum, temizle_h122

    assert engine.dialect.name == "postgresql", engine.dialect.name
    acilisa_cek()

    kosu = f"c_{uuid4().hex[:6]}"

    with TestClient(app) as c:
        ctx = oturum(c, "H122TestPassPgConc123!")
        h = ctx["h"]
        cid = ctx["cid"]
        uid = ctx["uid"]

        with SessionLocal() as db:
            res: dict[str, Any] = {}
            try:
                # 1. Müşteri
                r_cust = c.post("/api/customers", headers=h, json={"name": f"H122 Musteri {kosu}"})
                assert r_cust.status_code in (200, 201), r_cust.text
                cust_id = r_cust.json()["id"]
                res["cust_id"] = cust_id

                # 2. Harman vadeli sipariş
                order_id = int(
                    db.execute(
                        text(
                            """INSERT INTO orders(
                                customer_id, order_date, due_date, final_total, status, paid_amount,
                                payment_term, payment_method, company_id, document_no
                            ) VALUES (
                                :cust, '2025-12-01', '2026-01-01', 1000, 'completed', 0,
                                'HARMAN_VADELI', 'credit', :cid, :doc_no
                            ) RETURNING id"""
                        ),
                        {"cust": cust_id, "cid": cid, "doc_no": f"ORD-{kosu}"},
                    ).scalar_one()
                )
                res["order_id"] = order_id

                # 3. Vade farkı politikası
                existing_policy = db.execute(
                    text("SELECT id FROM late_fee_policies WHERE company_id=:cid AND active=TRUE"),
                    {"cid": cid},
                ).first()
                if not existing_policy:
                    db.execute(
                        text(
                            """INSERT INTO late_fee_policies(
                                company_id, annual_rate, day_count_basis, grace_days, tax_mode,
                                vat_rate, effective_from, active
                            ) VALUES (:cid, 73, 365, 0, 'NO_VAT', 0, '2025-01-01', TRUE)"""
                        ),
                        {"cid": cid},
                    )
                db.commit()

                # 4. Vade farkı taslağı oluştur ve onayla
                r_draft = c.post(
                    "/api/finance/late-fees/charges",
                    headers={**h, "Idempotency-Key": f"ik-draft-{kosu}"},
                    json={"order_id": order_id, "period_start": "2026-01-02", "period_end": "2026-01-31"},
                )
                assert r_draft.status_code in (200, 201), r_draft.text
                late_fee_doc_id = r_draft.json()["id"]
                res["late_fee_doc_id"] = late_fee_doc_id

                r_post = c.post(
                    f"/api/finance/late-fees/charges/{late_fee_doc_id}/post",
                    headers={**h, "Idempotency-Key": f"ik-post-{kosu}"},
                )
                assert r_post.status_code == 200, r_post.text
                assert r_post.json()["status"] == "posted"

                # 5. İki eşzamanlı istek aynı Idempotency-Key ile aynı belgeyi tersler
                rev_key = f"ik-conc-rev-{kosu}"
                engel = Barrier(2)

                def do_reversal():
                    with TestClient(app) as tc:
                        engel.wait()
                        return tc.post(
                            f"/api/finance/late-fees/charges/{late_fee_doc_id}/reversal",
                            headers={**h, "Idempotency-Key": rev_key},
                        )

                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(do_reversal), pool.submit(do_reversal)]
                    resp1 = futures[0].result()
                    resp2 = futures[1].result()

                # İkisi de 200 dönmeli
                assert resp1.status_code == 200, f"İstek 1 başarısız ({resp1.status_code}): {resp1.text}"
                assert resp2.status_code == 200, f"İstek 2 başarısız ({resp2.status_code}): {resp2.text}"

                # İkisi de AYNI ters kayıt id'sini dönmeli
                doc1 = resp1.json()
                doc2 = resp2.json()
                assert doc1["id"] == doc2["id"], f"Farklı ters kayıt belgeleri üretildi: {doc1['id']} != {doc2['id']}"
                assert doc1["reversal_of_document_id"] == late_fee_doc_id
                res["reversal_doc_id"] = doc1["id"]

                # Veritabanında TAM OLARAK BİR eksi tutarlı ters kayıt satırı olmalı
                db.expire_all()
                reversal_count = db.execute(
                    text(
                        """SELECT COUNT(*) FROM receivable_charge_documents
                        WHERE company_id=:cid AND reversal_of_document_id=:orig_id"""
                    ),
                    {"cid": cid, "orig_id": late_fee_doc_id},
                ).scalar_one()
                assert reversal_count == 1, f"Beklenen 1 ters kayıt satırı, bulunan: {reversal_count}"

                # Orijinal belge status='reversed' olmalı
                orig_status = db.execute(
                    text("SELECT status FROM receivable_charge_documents WHERE id=:id AND company_id=:cid"),
                    {"id": late_fee_doc_id, "cid": cid},
                ).scalar_one()
                assert orig_status == "reversed"

            finally:
                temizle_h122(db, cid, res, kosu=kosu)

    acilisa_cek()
