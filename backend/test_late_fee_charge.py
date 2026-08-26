from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.auth import required_permission
from app.late_fee_charge_engine import _calculation_snapshot
from app.receivables_engine import PrincipalTimeline


BACKEND = Path(__file__).resolve().parent


def test_late_fee_charge_uses_finance_permission() -> None:
    assert required_permission("POST", "/api/finance/late-fees/charges") == "finance"


def test_late_fee_charge_rejects_unsupported_tax_mode() -> None:
    timeline = PrincipalTimeline(
        order_id=1,
        customer_id=1,
        customer_name="Test",
        document_no="TEST-1",
        order_date=date(2026, 1, 1),
        due_date=date(2026, 1, 1),
        original_principal=Decimal("100"),
        changes=(),
    )
    with pytest.raises(HTTPException) as error:
        _calculation_snapshot(
            timeline,
            date(2026, 1, 2),
            date(2026, 1, 3),
            annual_rate="10",
            day_count_basis=365,
            grace_days=0,
            tax_mode="VAT_EXCLUSIVE",
            vat_rate="20",
        )
    assert error.value.status_code == 422


def test_aging_router_uses_shared_net_receivable_service() -> None:
    reports_source = (
        BACKEND / "app" / "routers" / "reports.py"
    ).read_text(encoding="utf-8")
    engine_source = (
        BACKEND / "app" / "receivables_engine.py"
    ).read_text(encoding="utf-8")
    assert "calculate_net_receivables" in reports_source
    assert "receivable_charge_documents" not in reports_source
    assert "receivable_charge_documents" in engine_source


def run_late_fee_charge_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_late_fee_charge_sqlite(tmp_path: Path) -> None:
    run_late_fee_charge_smoke(
        f"sqlite:///{(tmp_path / 'late-fee-charge.db').as_posix()}"
    )


_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.main import app


def auth_headers(client):
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(body["companies"][0]["id"]),
    }
    changed = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={
            "current_password": "admin123",
            "new_password": "LateFeeChargeAdmin123!",
        },
    )
    assert changed.status_code == 200, changed.text
    headers["Authorization"] = "Bearer " + changed.json()["access_token"]
    return headers


def create_user(client, admin_headers, username, role):
    initial_password = "LateFeeUser123!"
    created = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": username,
            "display_name": username,
            "password": initial_password,
            "role": role,
        },
    )
    assert created.status_code in (200, 201), created.text
    login = client.post(
        "/api/auth/login",
        json={"username": username, "password": initial_password},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(body["companies"][0]["id"]),
    }
    changed = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={
            "current_password": initial_password,
            "new_password": "LateFeeUser456!",
        },
    )
    assert changed.status_code == 200, changed.text
    headers["Authorization"] = "Bearer " + changed.json()["access_token"]
    return headers


with TestClient(app) as client:
    headers = auth_headers(client)
    cid = int(headers["X-Company-ID"])
    restricted_headers = create_user(
        client, headers, "late_fee_reporter", "rapor"
    )
    with SessionLocal() as db:
        customer_id = int(db.execute(
            text(
                "INSERT INTO customers(name,company_id) "
                "VALUES('Tahakkuk Cari',:cid) RETURNING id"
            ),
            {"cid": cid},
        ).scalar_one())
        order_id = int(db.execute(
            text(
                """INSERT INTO orders(
                    customer_id,order_date,due_date,final_total,status,paid_amount,
                    payment_term,payment_method,company_id,document_no
                ) VALUES(
                    :customer,'2025-12-01','2026-01-01',1000,'completed',0,
                    'HARMAN_VADELI','credit',:cid,'VF-CHARGE-1'
                ) RETURNING id"""
            ),
            {"customer": customer_id, "cid": cid},
        ).scalar_one())
        db.execute(
            text(
                """INSERT INTO late_fee_policies(
                    company_id,annual_rate,day_count_basis,grace_days,tax_mode,
                    vat_rate,effective_from,active
                ) VALUES(:cid,73,365,0,'NO_VAT',0,'2025-01-01',:active)"""
            ),
            {"cid": cid, "active": True},
        )
        company_b = int(db.execute(
            text(
                """INSERT INTO companies(name,is_active,created_at)
                VALUES('Late Fee Tenant B',TRUE,CURRENT_TIMESTAMP) RETURNING id"""
            )
        ).scalar_one())
        admin_id = int(db.execute(
            text("SELECT id FROM app_users WHERE username='admin'")
        ).scalar_one())
        db.execute(
            text(
                """INSERT INTO user_company_memberships(
                    user_id,company_id,is_default,created_at
                ) VALUES(:user_id,:company_id,FALSE,CURRENT_TIMESTAMP)"""
            ),
            {"user_id": admin_id, "company_id": company_b},
        )
        db.commit()
    tenant_b_headers = {**headers, "X-Company-ID": str(company_b)}

    payload = {
        "order_id": order_id,
        "period_start": "2026-01-02",
        "period_end": "2026-01-10",
    }
    draft_headers = {**headers, "Idempotency-Key": "draft-1"}
    draft = client.post(
        "/api/finance/late-fees/charges",
        headers=draft_headers,
        json=payload,
    )
    assert draft.status_code == 200, draft.text
    draft_body = draft.json()
    assert draft_body["status"] == "draft"
    assert draft_body["gross_amount"] == "18.00"
    assert draft_body["revision_no"] == 1
    own_get = client.get(
        f"/api/finance/late-fees/charges/{draft_body['id']}",
        headers=headers,
    )
    assert own_get.status_code == 200, own_get.text
    assert own_get.json()["id"] == draft_body["id"]

    cross_tenant_get = client.get(
        f"/api/finance/late-fees/charges/{draft_body['id']}",
        headers=tenant_b_headers,
    )
    assert cross_tenant_get.status_code == 404, cross_tenant_get.text
    cross_tenant_create = client.post(
        "/api/finance/late-fees/charges",
        headers={**tenant_b_headers, "Idempotency-Key": "tenant-b-create"},
        json=payload,
    )
    assert cross_tenant_create.status_code == 404, cross_tenant_create.text
    for suffix in ("post", "reversal"):
        denied = client.post(
            f"/api/finance/late-fees/charges/{draft_body['id']}/{suffix}",
            headers={
                **tenant_b_headers,
                "Idempotency-Key": f"tenant-b-{suffix}",
            },
        )
        assert denied.status_code == 404, denied.text

    restricted_create = client.post(
        "/api/finance/late-fees/charges",
        headers={**restricted_headers, "Idempotency-Key": "rbac-create"},
        json=payload,
    )
    assert restricted_create.status_code == 403, restricted_create.text
    restricted_get = client.get(
        f"/api/finance/late-fees/charges/{draft_body['id']}",
        headers=restricted_headers,
    )
    assert restricted_get.status_code == 403, restricted_get.text
    for suffix in ("post", "reversal"):
        denied = client.post(
            f"/api/finance/late-fees/charges/{draft_body['id']}/{suffix}",
            headers={
                **restricted_headers,
                "Idempotency-Key": f"rbac-{suffix}",
            },
        )
        assert denied.status_code == 403, denied.text

    replay = client.post(
        "/api/finance/late-fees/charges",
        headers=draft_headers,
        json=payload,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == draft_body["id"]

    natural_retry = client.post(
        "/api/finance/late-fees/charges",
        headers={**headers, "Idempotency-Key": "draft-natural-retry"},
        json=payload,
    )
    assert natural_retry.status_code == 200, natural_retry.text
    assert natural_retry.json()["id"] == draft_body["id"]

    before_post = client.get(
        "/api/reports/receivables-aging",
        headers=headers,
        params={"as_of": "2026-01-10"},
    )
    assert before_post.status_code == 200, before_post.text
    assert all(
        row["document_type"] != "late_fee"
        for customer in before_post.json()["customers"]
        for row in customer["documents"]
    )

    post_headers = {**headers, "Idempotency-Key": "post-1"}
    posted = client.post(
        f"/api/finance/late-fees/charges/{draft_body['id']}/post",
        headers=post_headers,
    )
    assert posted.status_code == 200, posted.text
    assert posted.json()["status"] == "posted"
    posted_amount = posted.json()["gross_amount"]

    with SessionLocal() as db:
        post_row_counts = {
            "documents": int(db.execute(text(
                "SELECT COUNT(*) FROM receivable_charge_documents WHERE company_id=:cid"
            ), {"cid": cid}).scalar_one()),
            "requests": int(db.execute(text(
                """SELECT COUNT(*) FROM receivable_charge_idempotency
                WHERE company_id=:cid AND operation_type='post_late_fee_document'"""
            ), {"cid": cid}).scalar_one()),
        }
    new_key_without_drift = client.post(
        f"/api/finance/late-fees/charges/{draft_body['id']}/post",
        headers={**headers, "Idempotency-Key": "post-new-no-drift"},
    )
    assert new_key_without_drift.status_code == 409, new_key_without_drift.text

    with SessionLocal() as db:
        db.execute(
            text(
                """UPDATE late_fee_policies SET annual_rate=36.5
                WHERE company_id=:cid"""
            ),
            {"cid": cid},
        )
        db.commit()
    posted_replay = client.post(
        f"/api/finance/late-fees/charges/{draft_body['id']}/post",
        headers=post_headers,
    )
    assert posted_replay.status_code == 200, posted_replay.text
    assert posted_replay.json()["id"] == draft_body["id"]
    assert posted_replay.json()["gross_amount"] == posted_amount
    new_key_with_drift = client.post(
        f"/api/finance/late-fees/charges/{draft_body['id']}/post",
        headers={**headers, "Idempotency-Key": "post-new-with-drift"},
    )
    assert new_key_with_drift.status_code == 409, new_key_with_drift.text
    assert "zaten onaylı" in new_key_with_drift.json()["detail"]
    with SessionLocal() as db:
        assert db.execute(text(
            "SELECT COUNT(*) FROM receivable_charge_documents WHERE company_id=:cid"
        ), {"cid": cid}).scalar_one() == post_row_counts["documents"]
        assert db.execute(text(
            """SELECT COUNT(*) FROM receivable_charge_idempotency
            WHERE company_id=:cid AND operation_type='post_late_fee_document'"""
        ), {"cid": cid}).scalar_one() == post_row_counts["requests"]
        db.execute(
            text(
                """UPDATE late_fee_policies SET annual_rate=73
                WHERE company_id=:cid"""
            ),
            {"cid": cid},
        )
        db.commit()

    payment_count_before = None
    with SessionLocal() as db:
        payment_count_before = int(db.execute(
            text("SELECT COUNT(*) FROM payments WHERE company_id=:cid"),
            {"cid": cid},
        ).scalar_one())
    payment_payload = {
        "entity_type": "customer",
        "entity_id": customer_id,
        "amount": "1.00",
        "payment_date": "2026-01-10",
        "payment_method": "cash",
        "reference_type": "late_fee",
        "reference_id": draft_body["id"],
    }
    denied_payment = client.post(
        "/api/payments",
        headers=headers,
        json=payment_payload,
    )
    assert denied_payment.status_code == 422, denied_payment.text
    assert "vade farkı belgesine tahsis V2c'de" in denied_payment.json()["detail"]

    manual_payment = client.post(
        "/api/payments",
        headers=headers,
        json={
            "entity_type": "customer",
            "entity_id": customer_id,
            "amount": "2.00",
            "payment_date": "2026-01-10",
            "payment_method": "cash",
        },
    )
    assert manual_payment.status_code == 201, manual_payment.text
    denied_update = client.put(
        f"/api/payments/{manual_payment.json()['id']}",
        headers=headers,
        json=payment_payload,
    )
    assert denied_update.status_code == 422, denied_update.text
    with SessionLocal() as db:
        assert db.execute(
            text("SELECT COUNT(*) FROM payments WHERE company_id=:cid"),
            {"cid": cid},
        ).scalar_one() == payment_count_before + 1
        unchanged = db.execute(
            text(
                """SELECT amount,reference_type,reference_id FROM payments
                WHERE id=:id AND company_id=:cid"""
            ),
            {"id": manual_payment.json()["id"], "cid": cid},
        ).mappings().one()
        assert Decimal(str(unchanged["amount"])) == Decimal("2.00")
        assert unchanged["reference_type"] is None
        assert unchanged["reference_id"] is None

    after_post = client.get(
        "/api/reports/receivables-aging",
        headers=headers,
        params={"as_of": "2026-01-10"},
    )
    late_fee_rows = [
        row
        for customer in after_post.json()["customers"]
        for row in customer["documents"]
        if row["document_type"] == "late_fee"
    ]
    assert len(late_fee_rows) == 1
    assert late_fee_rows[0]["remaining"] == "18.00"

    reversed_response = client.post(
        f"/api/finance/late-fees/charges/{draft_body['id']}/reversal",
        headers={**headers, "Idempotency-Key": "reverse-1"},
    )
    assert reversed_response.status_code == 200, reversed_response.text
    reversal = reversed_response.json()
    assert reversal["status"] == "posted"
    assert reversal["gross_amount"] == "-18.00"
    assert reversal["reversal_of_document_id"] == draft_body["id"]
    assert reversal["revision_no"] == 2
    reversal_replay = client.post(
        f"/api/finance/late-fees/charges/{draft_body['id']}/reversal",
        headers={**headers, "Idempotency-Key": "reverse-1"},
    )
    assert reversal_replay.status_code == 200, reversal_replay.text
    assert reversal_replay.json()["id"] == reversal["id"]
    with SessionLocal() as db:
        assert db.execute(
            text(
                """SELECT COUNT(*) FROM receivable_charge_documents
                WHERE company_id=:cid AND reversal_of_document_id=:document_id"""
            ),
            {"cid": cid, "document_id": draft_body["id"]},
        ).scalar_one() == 1

    replacement = client.post(
        "/api/finance/late-fees/charges",
        headers={**headers, "Idempotency-Key": "replacement-1"},
        json=payload,
    )
    assert replacement.status_code == 200, replacement.text
    assert replacement.json()["charge_period_id"] == draft_body["charge_period_id"]
    assert replacement.json()["revision_no"] == 3
    assert replacement.json()["status"] == "draft"

    after_reversal = client.get(
        "/api/reports/receivables-aging",
        headers=headers,
        params={"as_of": "2026-01-10"},
    )
    late_fee_total = sum(
        Decimal(row["remaining"])
        for customer in after_reversal.json()["customers"]
        for row in customer["documents"]
        if row["document_type"] == "late_fee"
    )
    assert late_fee_total == Decimal("0.00")

    second_period = client.post(
        "/api/finance/late-fees/charges",
        headers={**headers, "Idempotency-Key": "draft-2"},
        json={
            "order_id": order_id,
            "period_start": "2026-01-11",
            "period_end": "2026-01-20",
        },
    )
    assert second_period.status_code == 200, second_period.text
    with SessionLocal() as db:
        db.execute(
            text(
                """UPDATE late_fee_policies SET annual_rate=36.5
                WHERE company_id=:cid"""
            ),
            {"cid": cid},
        )
        db.commit()
    policy_changed_replay = client.post(
        "/api/finance/late-fees/charges",
        headers={**headers, "Idempotency-Key": "draft-2"},
        json={
            "order_id": order_id,
            "period_start": "2026-01-11",
            "period_end": "2026-01-20",
        },
    )
    assert policy_changed_replay.status_code == 200, policy_changed_replay.text
    assert policy_changed_replay.json()["id"] == second_period.json()["id"]
    policy_stale = client.post(
        f"/api/finance/late-fees/charges/{second_period.json()['id']}/post",
        headers={**headers, "Idempotency-Key": "post-policy-stale"},
    )
    assert policy_stale.status_code == 409, policy_stale.text
    assert "politikası değişti" in policy_stale.json()["detail"]

    with SessionLocal() as db:
        db.execute(
            text(
                """UPDATE late_fee_policies SET annual_rate=73
                WHERE company_id=:cid"""
            ),
            {"cid": cid},
        )
        db.commit()
    restored_post = client.post(
        f"/api/finance/late-fees/charges/{second_period.json()['id']}/post",
        headers={**headers, "Idempotency-Key": "post-policy-restored"},
    )
    assert restored_post.status_code == 200, restored_post.text

    policy_config_db = SessionLocal()
    try:
        db = policy_config_db
        charge_rows_before = {
            "periods": int(db.execute(text(
                "SELECT COUNT(*) FROM receivable_charge_periods WHERE company_id=:cid"
            ), {"cid": cid}).scalar_one()),
            "documents": int(db.execute(text(
                "SELECT COUNT(*) FROM receivable_charge_documents WHERE company_id=:cid"
            ), {"cid": cid}).scalar_one()),
            "requests": int(db.execute(text(
                "SELECT COUNT(*) FROM receivable_charge_idempotency WHERE company_id=:cid"
            ), {"cid": cid}).scalar_one()),
        }
        dialect = db.get_bind().dialect.name
        if dialect != "sqlite":
            db.execute(text(
                """ALTER TABLE late_fee_policies
                DROP CONSTRAINT ck_late_fee_policy_tax_mode"""
            ))
            db.commit()
        for index, tax_mode in enumerate(("INCLUSIVE", "EXCLUSIVE"), start=1):
            if dialect == "sqlite":
                db.execute(text("PRAGMA ignore_check_constraints = ON"))
            db.execute(
                text(
                    """UPDATE late_fee_policies SET tax_mode=:tax_mode
                    WHERE company_id=:cid"""
                ),
                {"tax_mode": tax_mode, "cid": cid},
            )
            if dialect == "sqlite":
                db.execute(text("PRAGMA ignore_check_constraints = OFF"))
            db.commit()
            rejected = client.post(
                "/api/finance/late-fees/charges",
                headers={
                    **headers,
                    "Idempotency-Key": f"tax-mode-{tax_mode.lower()}",
                },
                json={
                    "order_id": order_id,
                    "period_start": f"2026-02-0{index}",
                    "period_end": f"2026-02-0{index + 1}",
                },
            )
            assert rejected.status_code == 422, rejected.text
        assert db.execute(text(
            "SELECT COUNT(*) FROM receivable_charge_periods WHERE company_id=:cid"
        ), {"cid": cid}).scalar_one() == charge_rows_before["periods"]
        assert db.execute(text(
            "SELECT COUNT(*) FROM receivable_charge_documents WHERE company_id=:cid"
        ), {"cid": cid}).scalar_one() == charge_rows_before["documents"]
        assert db.execute(text(
            "SELECT COUNT(*) FROM receivable_charge_idempotency WHERE company_id=:cid"
        ), {"cid": cid}).scalar_one() == charge_rows_before["requests"]
        db.execute(
            text(
                """UPDATE late_fee_policies SET tax_mode='NO_VAT'
                WHERE company_id=:cid"""
            ),
            {"cid": cid},
        )
        if dialect != "sqlite":
            db.execute(text(
                """ALTER TABLE late_fee_policies
                ADD CONSTRAINT ck_late_fee_policy_tax_mode
                CHECK (tax_mode = 'NO_VAT')"""
            ))
        db.commit()
    finally:
        policy_config_db.close()

    timeline_period = client.post(
        "/api/finance/late-fees/charges",
        headers={**headers, "Idempotency-Key": "draft-3"},
        json={
            "order_id": order_id,
            "period_start": "2026-01-21",
            "period_end": "2026-01-31",
        },
    )
    assert timeline_period.status_code == 200, timeline_period.text
    with SessionLocal() as db:
        db.execute(
            text(
                """INSERT INTO payments(
                    entity_type,entity_id,amount,payment_date,company_id,
                    payment_method,reference_type,reference_id
                ) VALUES(
                    'customer',:customer,100,'2026-01-15',:cid,
                    'cash','order',:order_id
                )"""
            ),
            {"customer": customer_id, "cid": cid, "order_id": order_id},
        )
        db.commit()
    stale_post = client.post(
        f"/api/finance/late-fees/charges/{timeline_period.json()['id']}/post",
        headers={**headers, "Idempotency-Key": "post-stale"},
    )
    assert stale_post.status_code == 409, stale_post.text

    installment_payload = {
        "order_id": order_id,
        "installment_id": 42,
        "period_start": "2026-03-01",
        "period_end": "2026-03-10",
    }
    installment_draft = client.post(
        "/api/finance/late-fees/charges",
        headers={**headers, "Idempotency-Key": "installment-create"},
        json=installment_payload,
    )
    assert installment_draft.status_code == 200, installment_draft.text
    installment_retry = client.post(
        "/api/finance/late-fees/charges",
        headers={**headers, "Idempotency-Key": "installment-natural-retry"},
        json=installment_payload,
    )
    assert installment_retry.status_code == 200, installment_retry.text
    assert installment_retry.json()["id"] == installment_draft.json()["id"]
    with SessionLocal() as db:
        installment_period = db.execute(
            text(
                """SELECT installment_id,COUNT(*) OVER() row_count
                FROM receivable_charge_periods
                WHERE company_id=:cid AND order_id=:order_id
                  AND installment_id=:installment_id"""
            ),
            {"cid": cid, "order_id": order_id, "installment_id": 42},
        ).mappings().one()
        assert installment_period["installment_id"] == 42
        assert installment_period["row_count"] == 1

    with SessionLocal() as db:
        assert db.execute(
            text(
                """SELECT COUNT(*) FROM receivable_charge_periods
                WHERE company_id=:cid AND order_id=:order_id
                  AND period_start='2026-01-02' AND period_end='2026-01-10'"""
            ),
            {"cid": cid, "order_id": order_id},
        ).scalar_one() == 1
        try:
            db.execute(
                text(
                    """INSERT INTO receivable_charge_periods(
                        company_id,order_id,period_start,period_end,charge_kind
                    ) VALUES(
                        :cid,:order_id,'2026-01-02','2026-01-10','late_fee'
                    )"""
                ),
                {"cid": cid, "order_id": order_id},
            )
            db.commit()
            raise AssertionError("duplicate natural period unexpectedly accepted")
        except IntegrityError:
            db.rollback()
'''
