"""H122: Vade farkı tahakkuk uçları yalnız charge_type='late_fee' belgelerine dokunur.

Ortak senaryo: SQLite testi ve PostgreSQL ikizi tarafından çağrılır.
1. bounced_check belgesi:
   - POST /charges/{id}/reversal -> 404 "Tahakkuk belgesi bulunamadı" (develop'ta 500)
   - GET /charges/{id} -> 404 "Tahakkuk belgesi bulunamadı" (develop'ta 500)
   - POST /charges/{id}/post -> 404 "Tahakkuk belgesi bulunamadı" (develop'ta 409)
2. service_fee belgesi:
   - POST /charges/{id}/reversal -> 404 (develop'ta 500)
   - GET /charges/{id} -> 404 (develop'ta 500)
   - POST /charges/{id}/post -> 404 (develop'ta 409)
3. 404 sonrası aynı Idempotency-Key ile tekrar:
   - receivable_charge_idempotency tablosunda kayıt bırakmaz (claim yanmaz).
   - Aynı anahtar ile tekrarlandığında 409 yerine yine 404 döner.
4. Gerçek late_fee belgesi:
   - GET -> 200
   - POST /charges/{id}/reversal -> 200, ters kayıt satırı üretir (-gross_amount, -net_amount, -vat_amount).
   - Orijinal belge status='reversed' olur.
   - Aynı Idempotency-Key ile tekrar -> 200 replay (aynı ters kayıt belgesi).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from sqlalchemy import text


def run_h122_scenario(client, headers: dict[str, str], cid: int, uid: int, db, kosu: str) -> dict[str, Any]:
    # 1. Müşteri oluştur (API üzerinden)
    r_cust = client.post("/api/customers", headers=headers, json={"name": f"H122 Musteri {kosu}"})
    assert r_cust.status_code in (200, 201), r_cust.text
    cust_id = r_cust.json()["id"]

    # 2. Çek / Senet ve bounced_check belgesi oluştur
    cek_id = int(
        db.execute(
            text(
                """INSERT INTO cek_senetler(
                    company_id, tur, yon, portfoy_durumu, customer_id, tutar, vade, seri_no, created_at
                ) VALUES (
                    :cid, 'cek', 'alinan', 'karsiliksiz', :cust, 1000, '2026-10-01', :seri, CURRENT_TIMESTAMP
                ) RETURNING id"""
            ),
            {"cid": cid, "cust": cust_id, "seri": f"CHK-{kosu}"},
        ).scalar_one()
    )

    bounced_doc_id = int(
        db.execute(
            text(
                """INSERT INTO receivable_charge_documents(
                    company_id, cek_senet_id, customer_id, charge_type,
                    period_start, period_end, due_date_snapshot,
                    calculation_snapshot, gross_amount, status,
                    calculation_fingerprint, revision_no, currency, exchange_rate,
                    created_by, posted_by, created_at, posted_at
                ) VALUES (
                    :cid, :cek_id, :cust, 'bounced_check',
                    '2026-09-01', '2026-09-01', '2026-10-01',
                    '{}', 1000, 'posted',
                    :fp, 1, 'TRY', 1,
                    :uid, :uid, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                ) RETURNING id"""
            ),
            {"cid": cid, "cek_id": cek_id, "cust": cust_id, "fp": f"fp-chk-{kosu}", "uid": uid},
        ).scalar_one()
    )
    db.commit()

    # 3. Makine, iş emri ve service_fee belgesi oluştur (API üzerinden COMPLETED yapılarak servis borcu doğurulur)
    r_mach = client.post(
        "/api/machines",
        headers=headers,
        json={"customer_id": cust_id, "brand": "Brand", "model": "Model", "serial_number": f"SN-{kosu}"},
    )
    assert r_mach.status_code in (200, 201), r_mach.text
    machine_id = r_mach.json()["id"]

    r_wo = client.post(
        "/api/work-orders",
        headers=headers,
        json={
            "machine_id": machine_id,
            "customer_id": cust_id,
            "technician_id": uid,
            "actual_hours": "1",
            "labor_rate": "100",
            "warranty_type": "NONE",
            "warranty_percent": "0",
        },
    )
    assert r_wo.status_code in (200, 201), r_wo.text
    wo_id = r_wo.json()["id"]

    for st in ("IN_PROGRESS", "COMPLETED"):
        r_st = client.patch(f"/api/work-orders/{wo_id}/status", headers=headers, json={"status": st})
        assert r_st.status_code == 200, r_st.text

    service_doc_id = int(
        db.execute(
            text(
                """SELECT id FROM receivable_charge_documents
                WHERE company_id=:cid AND work_order_id=:wo_id AND charge_type='service_fee'"""
            ),
            {"cid": cid, "wo_id": wo_id},
        ).scalar_one()
    )

    # 4. Gerçek late_fee belgesi için sipariş ve politika oluştur
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

    # Mevcut aktif politika yoksa ekle
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

    # Vade farkı taslağı oluştur ve onayla
    r_draft = client.post(
        "/api/finance/late-fees/charges",
        headers={**headers, "Idempotency-Key": f"ik-draft-{kosu}"},
        json={"order_id": order_id, "period_start": "2026-01-02", "period_end": "2026-01-31"},
    )
    assert r_draft.status_code in (200, 201), r_draft.text
    late_fee_doc_id = r_draft.json()["id"]

    r_post_real = client.post(
        f"/api/finance/late-fees/charges/{late_fee_doc_id}/post",
        headers={**headers, "Idempotency-Key": f"ik-post-{kosu}"},
    )
    assert r_post_real.status_code == 200, r_post_real.text
    original_doc = r_post_real.json()
    assert original_doc["status"] == "posted"

    # ==================== TESTLER ====================

    # A. Karşılıksız çek belgesi testleri (404)
    rev_key_bounced = f"ik-rev-chk-{kosu}"
    r_rev_bounced = client.post(
        f"/api/finance/late-fees/charges/{bounced_doc_id}/reversal",
        headers={**headers, "Idempotency-Key": rev_key_bounced},
    )
    assert r_rev_bounced.status_code == 404, r_rev_bounced.text
    assert r_rev_bounced.json()["detail"] == "Tahakkuk belgesi bulunamadı"

    r_get_bounced = client.get(
        f"/api/finance/late-fees/charges/{bounced_doc_id}",
        headers=headers,
    )
    assert r_get_bounced.status_code == 404, r_get_bounced.text
    assert r_get_bounced.json()["detail"] == "Tahakkuk belgesi bulunamadı"

    post_key_bounced = f"ik-post-chk-{kosu}"
    r_post_bounced = client.post(
        f"/api/finance/late-fees/charges/{bounced_doc_id}/post",
        headers={**headers, "Idempotency-Key": post_key_bounced},
    )
    assert r_post_bounced.status_code == 404, r_post_bounced.text
    assert r_post_bounced.json()["detail"] == "Tahakkuk belgesi bulunamadı"

    # B. Servis cari borcu belgesi testleri (404)
    rev_key_svc = f"ik-rev-svc-{kosu}"
    r_rev_svc = client.post(
        f"/api/finance/late-fees/charges/{service_doc_id}/reversal",
        headers={**headers, "Idempotency-Key": rev_key_svc},
    )
    assert r_rev_svc.status_code == 404, r_rev_svc.text
    assert r_rev_svc.json()["detail"] == "Tahakkuk belgesi bulunamadı"

    r_get_svc = client.get(
        f"/api/finance/late-fees/charges/{service_doc_id}",
        headers=headers,
    )
    assert r_get_svc.status_code == 404, r_get_svc.text
    assert r_get_svc.json()["detail"] == "Tahakkuk belgesi bulunamadı"

    post_key_svc = f"ik-post-svc-{kosu}"
    r_post_svc = client.post(
        f"/api/finance/late-fees/charges/{service_doc_id}/post",
        headers={**headers, "Idempotency-Key": post_key_svc},
    )
    assert r_post_svc.status_code == 404, r_post_svc.text
    assert r_post_svc.json()["detail"] == "Tahakkuk belgesi bulunamadı"

    # C. 404 sonrası aynı Idempotency-Key ile tekrar
    # Idempotency tablosunda talep bırakılmadığını doğrula
    claim_count = int(
        db.execute(
            text(
                """SELECT COUNT(*) FROM receivable_charge_idempotency
                WHERE company_id=:cid AND idempotency_key IN (:k1, :k2)"""
            ),
            {"cid": cid, "k1": rev_key_bounced, "k2": post_key_bounced},
        ).scalar_one()
    )
    assert claim_count == 0, f"404 dönen istek idempotency kaydı bırakmamalı, bulundu: {claim_count}"

    # Aynı Idempotency-Key ile tekrar çağrıldığında 409 değil yine 404 dönmelidir
    r_rev_bounced_retry = client.post(
        f"/api/finance/late-fees/charges/{bounced_doc_id}/reversal",
        headers={**headers, "Idempotency-Key": rev_key_bounced},
    )
    assert r_rev_bounced_retry.status_code == 404, r_rev_bounced_retry.text
    assert r_rev_bounced_retry.json()["detail"] == "Tahakkuk belgesi bulunamadı"

    r_post_bounced_retry = client.post(
        f"/api/finance/late-fees/charges/{bounced_doc_id}/post",
        headers={**headers, "Idempotency-Key": post_key_bounced},
    )
    assert r_post_bounced_retry.status_code == 404, r_post_bounced_retry.text
    assert r_post_bounced_retry.json()["detail"] == "Tahakkuk belgesi bulunamadı"

    # D. Gerçek late_fee belgesi ters kaydı (200)
    r_get_real = client.get(
        f"/api/finance/late-fees/charges/{late_fee_doc_id}",
        headers=headers,
    )
    assert r_get_real.status_code == 200, r_get_real.text
    assert r_get_real.json()["status"] == "posted"

    rev_key_real = f"ik-rev-real-{kosu}"
    r_rev_real = client.post(
        f"/api/finance/late-fees/charges/{late_fee_doc_id}/reversal",
        headers={**headers, "Idempotency-Key": rev_key_real},
    )
    assert r_rev_real.status_code == 200, r_rev_real.text
    reversal_doc = r_rev_real.json()

    # Ters kayıt özellikleri:
    assert reversal_doc["charge_type"] == "late_fee"
    assert reversal_doc["status"] == "posted"
    assert reversal_doc["reversal_of_document_id"] == late_fee_doc_id
    assert Decimal(str(reversal_doc["gross_amount"])) == -Decimal(str(original_doc["gross_amount"]))
    assert Decimal(str(reversal_doc["net_amount"])) == -Decimal(str(original_doc["net_amount"]))
    assert Decimal(str(reversal_doc["vat_amount"])) == -Decimal(str(original_doc["vat_amount"]))

    # Orijinal belge status='reversed' olmalı
    db.expire_all()
    orig_status = db.execute(
        text("SELECT status FROM receivable_charge_documents WHERE id=:id AND company_id=:cid"),
        {"id": late_fee_doc_id, "cid": cid},
    ).scalar_one()
    assert orig_status == "reversed"

    # Replay: aynı Idempotency-Key ile tekrar çağrıldığında 200 ve aynı ters kayıt döner
    r_rev_replay = client.post(
        f"/api/finance/late-fees/charges/{late_fee_doc_id}/reversal",
        headers={**headers, "Idempotency-Key": rev_key_real},
    )
    assert r_rev_replay.status_code == 200, r_rev_replay.text
    assert r_rev_replay.json()["id"] == reversal_doc["id"]

    return {
        "cust_id": cust_id,
        "cek_id": cek_id,
        "wo_id": wo_id,
        "machine_id": machine_id,
        "order_id": order_id,
        "bounced_doc_id": bounced_doc_id,
        "service_doc_id": service_doc_id,
        "late_fee_doc_id": late_fee_doc_id,
        "reversal_doc_id": reversal_doc["id"],
    }


def oturum(client, yeni_parola: str = "H122TestPass123!") -> dict[str, Any]:
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()
    cid = login["companies"][0]["id"]
    uid = login["user"]["id"]
    h = {"Authorization": "Bearer " + login["access_token"], "X-Company-ID": str(cid)}
    changed = client.post(
        "/api/auth/change-password",
        headers=h,
        json={"current_password": "admin123", "new_password": yeni_parola},
    )
    h["Authorization"] = "Bearer " + changed.json()["access_token"]
    return {"h": h, "cid": cid, "uid": uid}


def temizle_h122(db, cid: int, res: dict[str, Any]) -> None:
    try:
        cust_id = res.get("cust_id")
        order_id = res.get("order_id")
        wo_id = res.get("wo_id")
        machine_id = res.get("machine_id")
        cek_id = res.get("cek_id")
        if cust_id:
            db.execute(text("DELETE FROM receivable_charge_documents WHERE customer_id=:c AND company_id=:cid"), {"c": cust_id, "cid": cid})
        if order_id:
            db.execute(text("DELETE FROM receivable_charge_periods WHERE order_id=:o AND company_id=:cid"), {"o": order_id, "cid": cid})
            db.execute(text("DELETE FROM orders WHERE id=:o AND company_id=:cid"), {"o": order_id, "cid": cid})
        if wo_id:
            db.execute(text("DELETE FROM work_orders WHERE id=:w AND company_id=:cid"), {"w": wo_id, "cid": cid})
        if machine_id:
            db.execute(text("DELETE FROM machines WHERE id=:m AND company_id=:cid"), {"m": machine_id, "cid": cid})
        if cek_id:
            db.execute(text("DELETE FROM cek_senetler WHERE id=:k AND company_id=:cid"), {"k": cek_id, "cid": cid})
        if cust_id:
            db.execute(text("DELETE FROM customers WHERE id=:c AND company_id=:cid"), {"c": cust_id, "cid": cid})
        db.commit()
    except Exception:
        db.rollback()

