"""Bildirimler FAZ-1 — uçtan uca API akışı (SQLite alt süreç).

Senaryo, F1'in "hiçbir mesaj kendiliğinden gitmez" sözleşmesini HTTP
seviyesinde doğrular:

1. Şablon sınıfsız oluşturulamaz; onaylanmadan gönderimde kullanılamaz.
2. Rıza olmadan hatırlatma kuyruğa GİRMEZ.
3. Kuyruğa giren satır ``AWAITING_APPROVAL`` doğar; onay ayrı yetkidir ve
   oluşturan kendi satırını onaylayamaz (dört-göz).
4. Onaysız satır dispatch edilemez.
5. Rıza geri çekilirse onaylı satır bile gönderilmez (fail-closed).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_notification_f1_api_flow(tmp_path: Path) -> None:
    database = tmp_path / "f1_api.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _F1_API_FLOW],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "F1_API_OK" in completed.stdout


_F1_API_FLOW = r'''
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app

BODY = "Sayin {musteri_adi}, {makine_adi} servis randevunuz {randevu_tarihi}. {firma_adi}"

with TestClient(app) as client:
    login = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert login.status_code == 200, login.text
    body = login.json()
    cid = body["companies"][0]["id"]
    headers = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(cid),
    }
    rotated = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": "admin123", "new_password": "F1ApiFlow123!"},
    )
    assert rotated.status_code == 200, rotated.text
    headers["Authorization"] = "Bearer " + rotated.json()["access_token"]

    # ---------------------------------------------------------------
    # 1) Şablon: sınıf zorunlu, link yasak, onaysız etkin değil
    # ---------------------------------------------------------------
    missing_class = client.post(
        "/api/notifications/templates",
        headers=headers,
        json={"code": "service.reminder", "channel": "SMS", "name": "S", "body": BODY},
    )
    assert missing_class.status_code == 422, missing_class.text

    with_link = client.post(
        "/api/notifications/templates",
        headers=headers,
        json={
            "code": "link.test",
            "channel": "SMS",
            "name": "Link",
            "body": "Odeme icin: http://odeme.example.com",
            "message_class": "SERVICE_TRANSACTIONAL",
        },
    )
    assert with_link.status_code == 400, with_link.text

    created = client.post(
        "/api/notifications/templates",
        headers=headers,
        json={
            "code": "service.reminder",
            "channel": "SMS",
            "name": "Servis hatirlatma",
            "body": BODY,
            "message_class": "SERVICE_TRANSACTIONAL",
        },
    )
    assert created.status_code == 201, created.text
    template_id = created.json()["id"]
    # Yeni şablon her zaman pasif doğar: "sınıf onayı bekliyor".
    assert not created.json()["is_active"]

    # ---------------------------------------------------------------
    # 2) İş emri + müşteri hazırlığı
    # ---------------------------------------------------------------
    customer = client.post(
        "/api/customers",
        headers=headers,
        json={"name": "Ayse Kaya", "phone": "0532 111 22 33"},
    )
    assert customer.status_code in (200, 201), customer.text
    customer_id = customer.json()["id"]

    machine = client.post(
        "/api/machines",
        headers=headers,
        json={
            "brand": "John Deere",
            "model": "6120M",
            "customer_id": customer_id,
        },
    )
    assert machine.status_code in (200, 201), machine.text
    machine_id = machine.json()["id"]

    technician = client.post(
        "/api/users",
        headers=headers,
        json={
            "username": "f1_teknisyen",
            "display_name": "Teknisyen",
            "password": "F1Teknisyen123!",
            "role": "depo",
        },
    )
    assert technician.status_code == 201, technician.text
    technician_id = technician.json()["id"]

    work_order = client.post(
        "/api/work-orders",
        headers=headers,
        json={
            "machine_id": machine_id,
            "customer_id": customer_id,
            "technician_id": technician_id,
            "status": "SCHEDULED",
            "scheduled_date": "2026-08-12T09:30:00+00:00",
            "complaint": "Periyodik bakim",
        },
    )
    assert work_order.status_code in (200, 201), work_order.text
    work_order_id = work_order.json()["id"]

    # ---------------------------------------------------------------
    # 3) Şablon onaysızken hatırlatma üretilemez
    # ---------------------------------------------------------------
    too_early = client.post(
        f"/api/notifications/work-orders/{work_order_id}/reminder",
        headers=headers,
        json={"channel": "SMS"},
    )
    assert too_early.status_code == 400, too_early.text
    assert "etkin bir sablon" in too_early.json()["detail"].replace("ş", "s").replace("İ", "I").lower() or "sablon" in too_early.json()["detail"].replace("ş", "s").lower()

    approved_template = client.post(
        f"/api/notifications/templates/{template_id}/approve", headers=headers
    )
    assert approved_template.status_code == 200, approved_template.text
    assert approved_template.json()["is_active"]

    # ---------------------------------------------------------------
    # 4) Rıza olmadan kuyruğa GİRMEZ (fail-closed)
    # ---------------------------------------------------------------
    without_consent = client.post(
        f"/api/notifications/work-orders/{work_order_id}/reminder",
        headers=headers,
        json={"channel": "SMS"},
    )
    assert without_consent.status_code == 400, without_consent.text

    consent = client.put(
        "/api/notifications/consents",
        headers=headers,
        json={
            "party_type": "CUSTOMER",
            "party_id": customer_id,
            "channel": "SMS",
            "granted": True,
            "source": "FORM",
            "recipient": "0532 111 22 33",
        },
    )
    assert consent.status_code == 200, consent.text
    consent_id = consent.json()["id"]
    assert consent.json()["status"] == "GRANTED"

    events = client.get(
        f"/api/notifications/consents/{consent_id}/events", headers=headers
    )
    assert events.status_code == 200, events.text
    assert events.json()[0]["action"] == "GRANT"

    # ---------------------------------------------------------------
    # 5) Hatırlatma kuyruğa girer — ONAY BEKLER, gönderilmez
    # ---------------------------------------------------------------
    queued = client.post(
        f"/api/notifications/work-orders/{work_order_id}/reminder",
        headers=headers,
        json={"channel": "SMS"},
    )
    assert queued.status_code == 201, queued.text
    notification_id = queued.json()["id"]
    assert queued.json()["status"] == "AWAITING_APPROVAL"
    content_hash = queued.json()["content_hash"]
    assert "Ayse Kaya" in queued.json()["body"]
    assert "12.08.2026" in queued.json()["body"]

    # Onaysız satır dispatch EDİLEMEZ.
    blocked = client.post(
        f"/api/notifications/{notification_id}/dispatch", headers=headers
    )
    assert blocked.status_code == 409, blocked.text

    # Dört-göz: oluşturan kendi satırını onaylayamaz.
    self_approve = client.post(
        f"/api/notifications/{notification_id}/approve",
        headers=headers,
        json={"seen_hash": content_hash},
    )
    assert self_approve.status_code == 409, self_approve.text
    assert "Kendi olu" in self_approve.json()["detail"]

    # Bayat önizleme reddedilir.
    approver = client.post(
        "/api/users",
        headers=headers,
        json={
            "username": "f1_onaylayan",
            "display_name": "Onaylayan",
            "password": "F1Onaylayan123!",
            "role": "yonetici",
        },
    )
    assert approver.status_code == 201, approver.text
    approver_login = client.post(
        "/api/auth/login",
        json={"username": "f1_onaylayan", "password": "F1Onaylayan123!"},
    )
    assert approver_login.status_code == 200, approver_login.text
    approver_headers = {
        "Authorization": "Bearer " + approver_login.json()["access_token"],
        "X-Company-ID": str(cid),
    }
    approver_rotated = client.post(
        "/api/auth/change-password",
        headers=approver_headers,
        json={
            "current_password": "F1Onaylayan123!",
            "new_password": "F1Onaylayan456!",
        },
    )
    assert approver_rotated.status_code == 200, approver_rotated.text
    approver_headers["Authorization"] = (
        "Bearer " + approver_rotated.json()["access_token"]
    )

    stale = client.post(
        f"/api/notifications/{notification_id}/approve",
        headers=approver_headers,
        json={"seen_hash": "0" * 64},
    )
    assert stale.status_code == 409, stale.text

    preview = client.get(
        f"/api/notifications/{notification_id}/preview", headers=approver_headers
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["can_approve"] is True
    assert preview.json()["content_hash"] == content_hash

    approved = client.post(
        f"/api/notifications/{notification_id}/approve",
        headers=approver_headers,
        json={"seen_hash": content_hash},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "PENDING"
    assert approved.json()["dispatch_armed"] is True

    # ---------------------------------------------------------------
    # 6) Rıza geri çekilirse onaylı satır bile gönderilmez
    # ---------------------------------------------------------------
    revoked = client.put(
        "/api/notifications/consents",
        headers=headers,
        json={
            "party_type": "CUSTOMER",
            "party_id": customer_id,
            "channel": "SMS",
            "granted": False,
            "source": "PHONE",
            "reason": "musteri istemedi",
        },
    )
    assert revoked.status_code == 200, revoked.text

    dispatched = client.post(
        f"/api/notifications/{notification_id}/dispatch", headers=headers
    )
    assert dispatched.status_code == 200, dispatched.text
    assert dispatched.json()["status"] == "FAILED"

    with SessionLocal() as db:
        row = db.execute(
            text(
                "SELECT status, consent_decision, consent_decision_reason "
                "FROM notifications WHERE id=:i AND company_id=:cid"
            ),
            {"i": notification_id, "cid": cid},
        ).mappings().one()
    assert row["status"] == "REJECTED"
    assert row["consent_decision"] == "BLOCKED"
    assert row["consent_decision_reason"] == "REVOKED"

    # Rıza engelli satır retry ile canlandırılamaz.
    retry = client.post(
        f"/api/notifications/{notification_id}/retry", headers=headers
    )
    assert retry.status_code == 409, retry.text

    # ---------------------------------------------------------------
    # 7) Sayaçlar: gönderilmeyecek satır "bekleyen" sayılmaz
    # ---------------------------------------------------------------
    counters = client.get("/api/notifications/counters", headers=headers)
    assert counters.status_code == 200, counters.text
    assert counters.json()["pending"] == 0
    assert counters.json()["stopped"] >= 1

    problem_tab = client.get(
        "/api/notifications/outbox", headers=headers, params={"tab": "problem"}
    )
    assert problem_tab.status_code == 200, problem_tab.text
    item = next(
        row for row in problem_tab.json()["items"] if row["id"] == notification_id
    )
    assert item["display_state"] == "İzin engeli nedeniyle gönderilmedi"
    assert "+905321112233" not in item["recipient"]

print("F1_API_OK")
'''
