"""H105: COMPLETED iş emrinde işçilik ve başlık düzenlemeleri servis alacağını uzlaştırır.

H102 parçalar için bunu yapmıştı (_reconcile_completed_receivable).
H105 ise işçilik satırları (create/update/approve/void) ve iş emri başlık güncellemesi (PUT)
için servis alacağının COMPLETED iş emrinde otomatik uzlaştırılmasını sağlar.

Testler:
(a) Onaylanan işçilik satırı -> alacak yeni müşteri payıyla yeniden yazılır, ters kayıt reason == 'labor_reconciliation'.
(b) Başlık PUT ile labor_rate değişimi -> alacak yeniden yazılır, reason == 'work_order_reconciliation'.
(c) Tutar-nötr başlık düzenlemesi (açıklama/özet vb.) -> yeni satır eklenmez.
(d) Garanti oranı değişimi -> müşteri payı buna göre değişir, reason == 'work_order_reconciliation'.
(e) Fatura kesildikten sonra düzenleme -> 409 (ensure_work_order_unbilled).
"""
from __future__ import annotations

import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

_CALISMA_ALANI = tempfile.mkdtemp(prefix="h105-labor-header-reconcile-")
os.environ["DATABASE_URL"] = "sqlite:///" + (
    os.path.join(_CALISMA_ALANI, "h105.db").replace(os.sep, "/")
)
os.environ["SUNGUR_DATA_DIR"] = _CALISMA_ALANI

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth_context(client):
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()
    cid = login["companies"][0]["id"]
    uid = login["user"]["id"]
    h = {"Authorization": "Bearer " + login["access_token"], "X-Company-ID": str(cid)}
    changed = client.post(
        "/api/auth/change-password",
        headers=h,
        json={"current_password": "admin123", "new_password": "H105TestPass123!"},
    )
    h["Authorization"] = "Bearer " + changed.json()["access_token"]
    return {"c": client, "h": h, "cid": cid, "uid": uid}


def _get_receivables(cid: int, wid: int):
    with SessionLocal() as db:
        return db.execute(
            text(
                """SELECT id,revision_no,status,gross_amount,reversal_of_document_id,calculation_snapshot
                FROM receivable_charge_documents WHERE company_id=:cid AND work_order_id=:wid
                ORDER BY revision_no"""
            ),
            {"cid": cid, "wid": wid},
        ).mappings().all()


def _create_completed_wo(c, h, uid, *, hours="1", rate="100", warranty_type="NONE", warranty_percent="0"):
    cust = c.post("/api/customers", headers=h, json={"name": f"H105 Cari {hours}-{rate}"}).json()
    machine = c.post(
        "/api/machines",
        headers=h,
        json={"customer_id": cust["id"], "brand": "Brand", "model": "Model", "serial_number": f"H105-M-{cust['id']}"},
    ).json()

    wo = c.post(
        "/api/work-orders",
        headers=h,
        json={
            "machine_id": machine["id"],
            "customer_id": cust["id"],
            "technician_id": uid,
            "actual_hours": hours,
            "labor_rate": rate,
            "warranty_type": warranty_type,
            "warranty_percent": warranty_percent,
        },
    ).json()
    wid = wo["id"]

    for st in ("IN_PROGRESS", "COMPLETED"):
        r = c.patch(f"/api/work-orders/{wid}/status", headers=h, json={"status": st})
        assert r.status_code == 200, r.text
    return wid, cust["id"], machine["id"]


def test_h105_a_approve_labor_line_reconciles(auth_context):
    """(a) COMPLETED iş emrinde işçilik satırı onaylandığında alacak güncellenir ve reason == 'labor_reconciliation' olur."""
    c = auth_context["c"]
    h = auth_context["h"]
    cid = auth_context["cid"]
    uid = auth_context["uid"]

    # 1 saat @ 100 TRY = 100 TRY + %20 KDV = 120.00 TRY
    wid, cust_id, machine_id = _create_completed_wo(c, h, uid, hours="1", rate="100")

    docs = _get_receivables(cid, wid)
    assert len(docs) == 1
    assert Decimal(str(docs[0]["gross_amount"])) == Decimal("120.00")

    # Taslak işçilik satırı ekle (2 saat @ 150 TRY = 300 TRY + %20 KDV = 360.00 TRY)
    line_resp = c.post(
        f"/api/work-orders/{wid}/labor-lines",
        headers=h,
        json={"technician_user_id": uid, "hours": "2", "hourly_rate": "150", "note": "H105 test iscilik"},
    )
    assert line_resp.status_code == 201, line_resp.text
    line_id = line_resp.json()["id"]

    # Taslak satır alacağı DEĞİŞTİRMEZ (tutarı etkilemez)
    docs_after_draft = _get_receivables(cid, wid)
    assert len(docs_after_draft) == 1

    # İşçilik satırını onayla -> alacak güncellenmeli!
    appr_resp = c.post(f"/api/work-orders/{wid}/labor-lines/{line_id}/approve", headers=h)
    assert appr_resp.status_code == 200, appr_resp.text

    docs_after_approve = _get_receivables(cid, wid)
    assert len(docs_after_approve) == 3, f"Beklenen 3 belge (R1, R2 ters kayıt, R3 yeni), bulunan: {len(docs_after_approve)}"

    # R2: ters kayıt
    rev = docs_after_approve[1]
    assert rev["status"] == "posted"
    assert rev["reversal_of_document_id"] == docs_after_approve[0]["id"]
    snap_rev = json.loads(rev["calculation_snapshot"])
    assert snap_rev["source"] == "reversal"
    assert snap_rev["reason"] == "labor_reconciliation", f"Beklenen 'labor_reconciliation', bulunan: {snap_rev.get('reason')}"

    # R3: yeni alacak
    new_doc = docs_after_approve[2]
    assert new_doc["status"] == "posted"
    assert Decimal(str(new_doc["gross_amount"])) == Decimal("360.00")


def test_h105_b_header_put_labor_rate_reconciles(auth_context):
    """(b) Başlık PUT ile labor_rate değiştiğinde alacak güncellenir ve reason == 'work_order_reconciliation' olur."""
    c = auth_context["c"]
    h = auth_context["h"]
    cid = auth_context["cid"]
    uid = auth_context["uid"]

    # 1 saat @ 100 TRY = 120.00 TRY
    wid, cust_id, machine_id = _create_completed_wo(c, h, uid, hours="1", rate="100")

    docs = _get_receivables(cid, wid)
    assert len(docs) == 1
    assert Decimal(str(docs[0]["gross_amount"])) == Decimal("120.00")

    # Başlık PUT ile labor_rate 150 yap (1 * 150 + %20 KDV = 180.00 TRY)
    put_resp = c.put(
        f"/api/work-orders/{wid}",
        headers=h,
        json={
            "machine_id": machine_id,
            "customer_id": cust_id,
            "technician_id": uid,
            "actual_hours": "1",
            "labor_rate": "150",
            "warranty_type": "NONE",
            "warranty_percent": "0",
        },
    )
    assert put_resp.status_code == 200, put_resp.text

    docs_after_put = _get_receivables(cid, wid)
    assert len(docs_after_put) == 3, f"Beklenen 3 belge (R1, R2 ters kayıt, R3 yeni), bulunan: {len(docs_after_put)}"

    # R2: ters kayıt
    rev = docs_after_put[1]
    assert rev["status"] == "posted"
    assert rev["reversal_of_document_id"] == docs_after_put[0]["id"]
    snap_rev = json.loads(rev["calculation_snapshot"])
    assert snap_rev["source"] == "reversal"
    assert snap_rev["reason"] == "work_order_reconciliation", f"Beklenen 'work_order_reconciliation', bulunan: {snap_rev.get('reason')}"

    # R3: yeni alacak
    new_doc = docs_after_put[2]
    assert new_doc["status"] == "posted"
    assert Decimal(str(new_doc["gross_amount"])) == Decimal("180.00")


def test_h105_c_amount_neutral_header_edit_no_new_rows(auth_context):
    """(c) Tutar-nötr başlık düzenlemesi (yalnızca açıklama/özet vb.) yeni satır eklemez."""
    c = auth_context["c"]
    h = auth_context["h"]
    cid = auth_context["cid"]
    uid = auth_context["uid"]

    wid, cust_id, machine_id = _create_completed_wo(c, h, uid, hours="1", rate="100")
    docs_before = _get_receivables(cid, wid)
    assert len(docs_before) == 1

    # Yalnız açıklama değiştir
    put_resp = c.put(
        f"/api/work-orders/{wid}",
        headers=h,
        json={
            "machine_id": machine_id,
            "customer_id": cust_id,
            "technician_id": uid,
            "actual_hours": "1",
            "labor_rate": "100",
            "warranty_type": "NONE",
            "warranty_percent": "0",
            "complaint": "Yeni şikayet detayı",
            "repair_summary": "Yeni tamir özeti",
            "technician_notes": "Teknisyen notu",
        },
    )
    assert put_resp.status_code == 200, put_resp.text

    docs_after = _get_receivables(cid, wid)
    assert len(docs_after) == 1
    assert docs_after[0]["id"] == docs_before[0]["id"]


def test_h105_d_warranty_share_change_reconciles(auth_context):
    """(d) Garanti oranı değişimi müşteri payını günceller, reason == 'work_order_reconciliation'."""
    c = auth_context["c"]
    h = auth_context["h"]
    cid = auth_context["cid"]
    uid = auth_context["uid"]

    # 2 saat @ 100 TRY = 200 + %20 KDV = 240.00 TRY
    wid, cust_id, machine_id = _create_completed_wo(c, h, uid, hours="2", rate="100", warranty_type="NONE", warranty_percent="0")

    docs = _get_receivables(cid, wid)
    assert len(docs) == 1
    assert Decimal(str(docs[0]["gross_amount"])) == Decimal("240.00")

    # Garanti %50 yap -> müşteri payı 120.00 TRY olmalı
    put_resp = c.put(
        f"/api/work-orders/{wid}",
        headers=h,
        json={
            "machine_id": machine_id,
            "customer_id": cust_id,
            "technician_id": uid,
            "actual_hours": "2",
            "labor_rate": "100",
            "warranty_type": "PARTIAL",
            "warranty_percent": "50",
        },
    )
    assert put_resp.status_code == 200, put_resp.text

    docs_after = _get_receivables(cid, wid)
    assert len(docs_after) == 3
    rev = docs_after[1]
    snap_rev = json.loads(rev["calculation_snapshot"])
    assert snap_rev["reason"] == "work_order_reconciliation"

    new_doc = docs_after[2]
    assert Decimal(str(new_doc["gross_amount"])) == Decimal("120.00")


def test_h105_e_after_invoice_issued_edits_rejected_409(auth_context):
    """(e) Fatura kesildikten sonra iş emri ve işçilik düzenlemeleri 409 döner."""
    c = auth_context["c"]
    h = auth_context["h"]
    cid = auth_context["cid"]
    uid = auth_context["uid"]

    wid, cust_id, machine_id = _create_completed_wo(c, h, uid, hours="1", rate="100")

    # Fatura kes
    inv = c.post(
        "/api/invoices/generate",
        headers=h,
        json={"work_order_id": wid, "branch_prefix": "H105"},
    )
    assert inv.status_code == 201, inv.text

    # Fatura sonrası işçilik ekleme -> 409
    line_resp = c.post(
        f"/api/work-orders/{wid}/labor-lines",
        headers=h,
        json={"technician_user_id": uid, "hours": "1", "hourly_rate": "100"},
    )
    assert line_resp.status_code == 409, line_resp.text

    # Fatura sonrası başlık PUT -> 409
    put_resp = c.put(
        f"/api/work-orders/{wid}",
        headers=h,
        json={
            "machine_id": machine_id,
            "customer_id": cust_id,
            "technician_id": uid,
            "actual_hours": "2",
            "labor_rate": "100",
            "warranty_type": "NONE",
            "warranty_percent": "0",
        },
    )
    assert put_resp.status_code == 409, put_resp.text


def test_h105_void_approved_labor_line_reconciles(auth_context):
    """Onaylı işçilik satırı iptal edildiğinde (void) alacak güncellenir ve reason == 'labor_reconciliation' olur."""
    c = auth_context["c"]
    h = auth_context["h"]
    cid = auth_context["cid"]
    uid = auth_context["uid"]

    # Başlangıçta 1 saat @ 100 TRY header labor = 120.00 TRY
    wid, cust_id, machine_id = _create_completed_wo(c, h, uid, hours="1", rate="100")

    # İşçilik satırı ekle (2 saat @ 150 TRY = 360.00 TRY) ve onayla
    line_resp = c.post(
        f"/api/work-orders/{wid}/labor-lines",
        headers=h,
        json={"technician_user_id": uid, "hours": "2", "hourly_rate": "150"},
    )
    line_id = line_resp.json()["id"]
    c.post(f"/api/work-orders/{wid}/labor-lines/{line_id}/approve", headers=h)

    # Şimdi onaylı satırı iptal et -> faturalama tekrar header labor'a (1 saat @ 100 TRY = 120.00 TRY) döner
    void_resp = c.delete(f"/api/work-orders/{wid}/labor-lines/{line_id}", headers=h)
    assert void_resp.status_code == 204, void_resp.text

    docs_after_void = _get_receivables(cid, wid)
    # Önceden 3 belge vardı, void sonrası 2 yeni belge eklenir (toplam 5 belge)
    assert len(docs_after_void) == 5
    rev = docs_after_void[3]
    snap_rev = json.loads(rev["calculation_snapshot"])
    assert snap_rev["reason"] == "labor_reconciliation"

    new_doc = docs_after_void[4]
    assert Decimal(str(new_doc["gross_amount"])) == Decimal("120.00")


def test_h105_addendum_status_patch_recompleted_reversal_reason(auth_context):
    """(H121 / Addendum) COMPLETED -> IN_PROGRESS -> change price -> COMPLETED again
    reconciles via PATCH /api/work-orders/{id}/status and sets reversal reason == 'work_order_reconciliation'.
    """
    c = auth_context["c"]
    h = auth_context["h"]
    cid = auth_context["cid"]
    uid = auth_context["uid"]

    # 1 saat @ 100 TRY = 120.00 TRY
    wid, cust_id, machine_id = _create_completed_wo(c, h, uid, hours="1", rate="100")
    docs = _get_receivables(cid, wid)
    assert len(docs) == 1
    assert Decimal(str(docs[0]["gross_amount"])) == Decimal("120.00")

    # DB uzerinden IN_PROGRESS durumuna geri al (aktif alacak korunur;
    # PATCH /status ucu aktif alacak varken IN_PROGRESS'e gecisi assert_work_order_receivable_inactive ile engeller)
    with SessionLocal() as db:
        db.execute(
            text("UPDATE work_orders SET status='IN_PROGRESS' WHERE id=:id AND company_id=:cid"),
            {"id": wid, "cid": cid},
        )
        db.commit()

    # IN_PROGRESS iken başlık fiyatını değiştir (1 saat @ 200 TRY = 240.00 TRY)
    # Status IN_PROGRESS olduğu için PUT sırasında reconcile_if_completed alacağı uzlaştırmaz.
    r_put = c.put(
        f"/api/work-orders/{wid}",
        headers=h,
        json={
            "machine_id": machine_id,
            "customer_id": cust_id,
            "technician_id": uid,
            "actual_hours": "1",
            "labor_rate": "200",
            "warranty_type": "NONE",
            "warranty_percent": "0",
        },
    )
    assert r_put.status_code == 200, r_put.text

    # Hâlâ tek belge var (uzlaştırma henüz tetiklenmedi)
    assert len(_get_receivables(cid, wid)) == 1

    # IN_PROGRESS -> COMPLETED durumuna geç (status PATCH uç noktası üzerinden)
    r_comp = c.patch(f"/api/work-orders/{wid}/status", headers=h, json={"status": "COMPLETED"})
    assert r_comp.status_code == 200, r_comp.text

    docs_after = _get_receivables(cid, wid)
    assert len(docs_after) == 3, f"Beklenen 3 belge (R1, R2 ters kayıt, R3 yeni), bulunan: {len(docs_after)}"

    # R2: ters kayıt sebebi work_order_reconciliation olmalı
    rev = docs_after[1]
    assert rev["status"] == "posted"
    assert rev["reversal_of_document_id"] == docs_after[0]["id"]
    snap_rev = json.loads(rev["calculation_snapshot"])
    assert snap_rev["source"] == "reversal"
    assert snap_rev["reason"] == "work_order_reconciliation", (
        f"Beklenen 'work_order_reconciliation', bulunan: {snap_rev.get('reason')}"
    )

    # R3: yeni alacak
    new_doc = docs_after[2]
    assert new_doc["status"] == "posted"
    assert Decimal(str(new_doc["gross_amount"])) == Decimal("240.00")

