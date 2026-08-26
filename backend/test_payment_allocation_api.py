from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


BACKEND = Path(__file__).resolve().parent


def run_payment_allocation_api_smoke(
    database_url: str,
    *,
    enabled: bool,
    concurrent: bool = False,
) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    env["PAYMENT_ALLOCATION_ENGINE_ENABLED"] = "true" if enabled else "false"
    env["PAYMENT_ALLOCATION_CLOSED_THROUGH"] = "2026-07-15"
    env["ALLOCATION_API_CONCURRENT"] = "1" if concurrent else "0"
    completed = subprocess.run(
        [sys.executable, "-c", _ENABLED_SMOKE if enabled else _DISABLED_SMOKE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_payment_allocation_api_sqlite(tmp_path: Path) -> None:
    run_payment_allocation_api_smoke(
        f"sqlite:///{(tmp_path / 'allocation-api-enabled.db').as_posix()}",
        enabled=True,
    )
    run_payment_allocation_api_smoke(
        f"sqlite:///{(tmp_path / 'allocation-api-disabled.db').as_posix()}",
        enabled=False,
    )


_ENABLED_SMOKE = r'''
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import os
from threading import Barrier

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app
from app.payment_allocation_engine import manual_allocate_payment


def dec(value):
    return Decimal(str(value))


def insert_receivable(db, cid, customer_id, due_date, total="100"):
    return int(db.execute(
        text(
            """INSERT INTO orders(
                customer_id,order_date,due_date,final_total,status,paid_amount,
                payment_term,payment_method,company_id
            ) VALUES(
                :customer,'2026-01-01',:due_date,:total,'completed',0,
                'HARMAN_VADELI','credit',:cid
            ) RETURNING id"""
        ),
        {
            "customer": customer_id,
            "due_date": due_date,
            "total": total,
            "cid": cid,
        },
    ).scalar_one())


def insert_payment(db, cid, customer_id, amount="80"):
    return int(db.execute(
        text(
            """INSERT INTO payments(
                entity_type,entity_id,amount,payment_date,company_id,payment_method
            ) VALUES(
                'customer',:customer,:amount,'2026-07-01',:cid,'cash'
            ) RETURNING id"""
        ),
        {"customer": customer_id, "amount": amount, "cid": cid},
    ).scalar_one())


def create_user(client, admin_headers, username, role):
    initial = "AllocationUser123!"
    rotated = "AllocationUser456!"
    created = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": username,
            "display_name": username,
            "password": initial,
            "role": role,
        },
    )
    assert created.status_code in (200, 201), created.text
    login = client.post(
        "/api/auth/login",
        json={"username": username, "password": initial},
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
        json={"current_password": initial, "new_password": rotated},
    )
    assert changed.status_code == 200, changed.text
    headers["Authorization"] = "Bearer " + changed.json()["access_token"]
    return headers


with TestClient(app) as client:
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    admin_headers = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(body["companies"][0]["id"]),
    }
    changed = client.post(
        "/api/auth/change-password",
        headers=admin_headers,
        json={
            "current_password": "admin123",
            "new_password": "AllocationApiAdmin123!",
        },
    )
    assert changed.status_code == 200, changed.text
    admin_headers["Authorization"] = "Bearer " + changed.json()["access_token"]
    cid = int(admin_headers["X-Company-ID"])

    roles = {
        role: create_user(client, admin_headers, f"a3_{role}", role)
        for role in ("muhasebe", "satis", "depo", "rapor")
    }
    with SessionLocal() as db:
        customer_id = int(db.execute(
            text(
                "INSERT INTO customers(name,company_id) "
                "VALUES('A3 Tahsis Cari',:cid) RETURNING id"
            ),
            {"cid": cid},
        ).scalar_one())
        first_order = insert_receivable(db, cid, customer_id, "2026-06-01")
        second_order = insert_receivable(db, cid, customer_id, "2026-07-01")
        payment_id = insert_payment(db, cid, customer_id)
        db.commit()

    read_paths = (
        f"/api/payment-allocations/payments/{payment_id}",
        f"/api/payment-allocations/orders/{first_order}",
    )
    for role in ("muhasebe", "satis", "depo", "rapor"):
        for path in read_paths:
            response = client.get(path, headers=roles[role])
            assert response.status_code == 200, (role, path, response.text)
    for role in ("satis", "depo", "rapor"):
        denied_report = client.get(
            "/api/payment-allocations/reconciliation",
            headers=roles[role],
        )
        assert denied_report.status_code == 403, (role, denied_report.text)
        denied_mutations = (
            client.post(
                "/api/payment-allocations/manual",
                headers={**roles[role], "Idempotency-Key": f"denied-manual-{role}"},
                json={
                    "payment_id": payment_id,
                    "order_id": first_order,
                    "amount": "1",
                    "effective_date": "2026-07-16",
                },
            ),
            client.post(
                "/api/payment-allocations/1/reversal",
                headers={**roles[role], "Idempotency-Key": f"denied-reverse-{role}"},
                json={"amount": "1", "effective_date": "2026-07-16"},
            ),
            client.post(
                "/api/payment-allocations/reallocation",
                headers={**roles[role], "Idempotency-Key": f"denied-move-{role}"},
                json={
                    "allocation_id": 1,
                    "target_order_id": second_order,
                    "amount": "1",
                    "effective_date": "2026-07-16",
                },
            ),
        )
        assert all(response.status_code == 403 for response in denied_mutations), role

    finance_headers = roles["muhasebe"]
    report = client.get(
        "/api/payment-allocations/reconciliation",
        headers=finance_headers,
    )
    assert report.status_code == 200, report.text
    assert {row["order_id"] for row in report.json()} >= {first_order, second_order}

    manual_headers = {**finance_headers, "Idempotency-Key": "manual-a3-1"}
    manual_payload = {
        "payment_id": payment_id,
        "order_id": first_order,
        "amount": "60.00",
        "effective_date": "2026-07-16",
        "note": "Elle tahsis",
    }
    first = client.post(
        "/api/payment-allocations/manual",
        headers=manual_headers,
        json=manual_payload,
    )
    replay = client.post(
        "/api/payment-allocations/manual",
        headers=manual_headers,
        json=manual_payload,
    )
    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert first.json() == replay.json()
    allocation_id = first.json()["allocation_id"]
    assert first.json()["amount"] == "60.00"
    receivables_after_manual = {
        row["id"]: row
        for row in client.get(
            "/api/receivables",
            headers=finance_headers,
        ).json()["receivables"]
    }
    assert receivables_after_manual[first_order]["outstanding"] == 40.0
    assert receivables_after_manual[second_order]["outstanding"] == 100.0
    conflict = client.post(
        "/api/payment-allocations/manual",
        headers=manual_headers,
        json={**manual_payload, "amount": "59.00"},
    )
    assert conflict.status_code == 409, conflict.text
    oversized = client.post(
        "/api/payment-allocations/manual",
        headers={**finance_headers, "Idempotency-Key": "manual-a3-too-large"},
        json={
            **manual_payload,
            "amount": "21.00",
            "note": "Ödeme kalanını aşar",
        },
    )
    assert oversized.status_code == 409, oversized.text

    closed = client.post(
        f"/api/payment-allocations/{allocation_id}/reversal",
        headers={**finance_headers, "Idempotency-Key": "reverse-a3-closed"},
        json={"amount": "1.00", "effective_date": "2026-07-15"},
    )
    assert closed.status_code == 409, closed.text
    partial = client.post(
        f"/api/payment-allocations/{allocation_id}/reversal",
        headers={**finance_headers, "Idempotency-Key": "reverse-a3-partial"},
        json={"amount": "20.00", "effective_date": "2026-07-16"},
    )
    assert partial.status_code == 200, partial.text
    assert partial.json()["net_amount"] == "40.00"
    reversal_id = partial.json()["reversal_allocation_id"]
    over_reversal = client.post(
        f"/api/payment-allocations/{allocation_id}/reversal",
        headers={**finance_headers, "Idempotency-Key": "reverse-a3-over"},
        json={"amount": "41.00", "effective_date": "2026-07-16"},
    )
    assert over_reversal.status_code == 409, over_reversal.text
    reverse_reversal = client.post(
        f"/api/payment-allocations/{reversal_id}/reversal",
        headers={**finance_headers, "Idempotency-Key": "reverse-a3-reversal"},
        json={"amount": "1.00", "effective_date": "2026-07-16"},
    )
    assert reverse_reversal.status_code == 422, reverse_reversal.text

    before_failed_move = None
    with SessionLocal() as db:
        before_failed_move = int(db.execute(
            text(
                "SELECT COUNT(*) FROM payment_allocations "
                "WHERE company_id=:cid AND payment_id=:payment_id"
            ),
            {"cid": cid, "payment_id": payment_id},
        ).scalar_one())
    failed_move = client.post(
        "/api/payment-allocations/reallocation",
        headers={**finance_headers, "Idempotency-Key": "move-a3-too-large"},
        json={
            "allocation_id": allocation_id,
            "target_order_id": second_order,
            "amount": "41.00",
            "effective_date": "2026-07-16",
        },
    )
    assert failed_move.status_code == 409, failed_move.text
    with SessionLocal() as db:
        assert int(db.execute(
            text(
                "SELECT COUNT(*) FROM payment_allocations "
                "WHERE company_id=:cid AND payment_id=:payment_id"
            ),
            {"cid": cid, "payment_id": payment_id},
        ).scalar_one()) == before_failed_move

    moved = client.post(
        "/api/payment-allocations/reallocation",
        headers={**finance_headers, "Idempotency-Key": "move-a3-1"},
        json={
            "allocation_id": allocation_id,
            "target_order_id": second_order,
            "amount": "10.00",
            "effective_date": "2026-07-17",
        },
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["amount"] == "10.00"
    completed = client.post(
        f"/api/payment-allocations/{allocation_id}/reversal",
        headers={**finance_headers, "Idempotency-Key": "reverse-a3-complete"},
        json={"amount": "30.00", "effective_date": "2026-07-18"},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["net_amount"] == "0.00"
    historical = client.get(
        f"/api/payment-allocations/payments/{payment_id}",
        headers=roles["satis"],
        params={"as_of": "2026-07-17"},
    )
    assert historical.status_code == 200, historical.text
    historical_original = next(
        row for row in historical.json() if row["id"] == allocation_id
    )
    assert historical_original["net_amount"] == "30.00"
    assert any(
        row["status"] == "not_effective"
        and row["effective_date"] == "2026-07-18"
        for row in historical.json()
    )
    future_reversal = client.post(
        f"/api/payment-allocations/{moved.json()['allocation_id']}/reversal",
        headers={**finance_headers, "Idempotency-Key": "reverse-a3-future"},
        json={"amount": "1.00", "effective_date": "2099-01-01"},
    )
    assert future_reversal.status_code == 422, future_reversal.text

    history = client.get(
        f"/api/payment-allocations/payments/{payment_id}",
        headers=roles["satis"],
    )
    assert history.status_code == 200, history.text
    original = next(row for row in history.json() if row["id"] == allocation_id)
    assert original["status"] == "reversed"
    assert original["net_amount"] == "0.00"
    assert sum(
        -dec(row["amount"]) if row["status"] == "reversal" else dec(row["amount"])
        for row in history.json()
    ) == dec("10.00")
    order_history = client.get(
        f"/api/payment-allocations/orders/{second_order}",
        headers=roles["rapor"],
    )
    assert order_history.status_code == 200, order_history.text
    assert any(row["amount"] == "10.00" for row in order_history.json())
    with SessionLocal() as db:
        paid_rows = db.execute(
            text(
                "SELECT id,paid_amount FROM orders "
                "WHERE id IN (:first,:second) ORDER BY id"
            ),
            {"first": first_order, "second": second_order},
        ).all()
        assert all(dec(row[1]) == dec("0.00") for row in paid_rows)

        direct_order = insert_receivable(db, cid, customer_id, "2026-08-15")
        direct_payment = int(db.execute(
            text(
                """INSERT INTO payments(
                    entity_type,entity_id,amount,payment_date,company_id,
                    payment_method,reference_type,reference_id
                ) VALUES(
                    'customer',:customer,100,'2026-07-01',:cid,
                    'cash','order',:order_id
                ) RETURNING id"""
            ),
            {"customer": customer_id, "cid": cid, "order_id": direct_order},
        ).scalar_one())
        direct_allocation = int(db.execute(
            text(
                """INSERT INTO payment_allocations(
                    company_id,payment_id,order_id,amount,allocation_type,effective_date
                ) VALUES(
                    :cid,:payment_id,:order_id,100,'document_create','2026-07-01'
                ) RETURNING id"""
            ),
            {
                "cid": cid,
                "payment_id": direct_payment,
                "order_id": direct_order,
            },
        ).scalar_one())
        db.execute(
            text(
                "UPDATE orders SET paid_amount=100 "
                "WHERE id=:order_id AND company_id=:cid"
            ),
            {"order_id": direct_order, "cid": cid},
        )
        db.commit()
    before_direct_reversal = {
        row["id"]: row
        for row in client.get(
            "/api/receivables",
            headers=finance_headers,
        ).json()["receivables"]
    }
    assert before_direct_reversal[direct_order]["outstanding"] == 0.0
    direct_reversal = client.post(
        f"/api/payment-allocations/{direct_allocation}/reversal",
        headers={**finance_headers, "Idempotency-Key": "reverse-a3-direct"},
        json={"amount": "100.00", "effective_date": "2026-07-16"},
    )
    assert direct_reversal.status_code == 200, direct_reversal.text
    after_direct_reversal = {
        row["id"]: row
        for row in client.get(
            "/api/receivables",
            headers=finance_headers,
        ).json()["receivables"]
    }
    assert after_direct_reversal[direct_order]["outstanding"] == 100.0
    with SessionLocal() as db:
        assert dec(db.execute(
            text(
                "SELECT paid_amount FROM orders "
                "WHERE id=:order_id AND company_id=:cid"
            ),
            {"order_id": direct_order, "cid": cid},
        ).scalar_one()) == dec("0.00")

        rollback_source = insert_receivable(
            db, cid, customer_id, "2026-08-20", "30"
        )
        rollback_target = insert_receivable(
            db, cid, customer_id, "2026-08-21", "5"
        )
        rollback_payment = insert_payment(db, cid, customer_id, "30")
        rollback_allocation = int(db.execute(
            text(
                """INSERT INTO payment_allocations(
                    company_id,payment_id,order_id,amount,allocation_type,effective_date
                ) VALUES(
                    :cid,:payment_id,:order_id,30,'manual','2026-07-01'
                ) RETURNING id"""
            ),
            {
                "cid": cid,
                "payment_id": rollback_payment,
                "order_id": rollback_source,
            },
        ).scalar_one())
        db.commit()

    midpoint_headers = {
        **finance_headers,
        "Idempotency-Key": "move-a3-midpoint-retry",
    }
    midpoint_payload = {
        "allocation_id": rollback_allocation,
        "target_order_id": rollback_target,
        "amount": "10.00",
        "effective_date": "2026-07-16",
    }
    midpoint_failure = client.post(
        "/api/payment-allocations/reallocation",
        headers=midpoint_headers,
        json=midpoint_payload,
    )
    assert midpoint_failure.status_code == 409, midpoint_failure.text
    with SessionLocal() as db:
        assert db.execute(
            text(
                """SELECT COUNT(*) FROM payment_allocations
                WHERE company_id=:cid AND reversal_of_allocation_id=:allocation_id"""
            ),
            {"cid": cid, "allocation_id": rollback_allocation},
        ).scalar_one() == 0
        assert db.execute(
            text(
                """SELECT COUNT(*) FROM payment_idempotency
                WHERE company_id=:cid AND operation_type='reallocate_allocation'
                  AND resource_type='allocation' AND resource_id=:resource_id
                  AND idempotency_key='move-a3-midpoint-retry'"""
            ),
            {"cid": cid, "resource_id": str(rollback_allocation)},
        ).scalar_one() == 0
        db.execute(
            text(
                "UPDATE orders SET final_total=20 "
                "WHERE id=:order_id AND company_id=:cid"
            ),
            {"order_id": rollback_target, "cid": cid},
        )
        db.commit()
    midpoint_retry = client.post(
        "/api/payment-allocations/reallocation",
        headers=midpoint_headers,
        json=midpoint_payload,
    )
    assert midpoint_retry.status_code == 200, midpoint_retry.text
    assert midpoint_retry.json()["amount"] == "10.00"

    with SessionLocal() as db:
        company_b = int(db.execute(
            text(
                """INSERT INTO companies(name,is_active,created_at)
                VALUES('A3 Tenant B',TRUE,CURRENT_TIMESTAMP) RETURNING id"""
            )
        ).scalar_one())
        customer_b = int(db.execute(
            text(
                "INSERT INTO customers(name,company_id) "
                "VALUES('A3 Tenant B Cari',:cid) RETURNING id"
            ),
            {"cid": company_b},
        ).scalar_one())
        order_b = insert_receivable(db, company_b, customer_b, "2026-08-01")
        payment_b = insert_payment(db, company_b, customer_b, "10")
        db.commit()
    assert client.get(
        f"/api/payment-allocations/payments/{payment_b}",
        headers=finance_headers,
    ).status_code == 404
    cross_tenant = client.post(
        "/api/payment-allocations/manual",
        headers={**finance_headers, "Idempotency-Key": "manual-cross-tenant"},
        json={
            "payment_id": payment_b,
            "order_id": order_b,
            "amount": "1.00",
            "effective_date": "2026-07-16",
        },
    )
    assert cross_tenant.status_code == 404, cross_tenant.text

    if os.environ.get("ALLOCATION_API_CONCURRENT") == "1":
        with SessionLocal() as db:
            race_customer = int(db.execute(
                text(
                    "INSERT INTO customers(name,company_id) "
                    "VALUES('A3 Yarış Cari',:cid) RETURNING id"
                ),
                {"cid": cid},
            ).scalar_one())
            race_order = insert_receivable(db, cid, race_customer, "2026-09-01")
            race_payment = insert_payment(db, cid, race_customer, "25")
            db.commit()
        barrier = Barrier(2)

        def race():
            with SessionLocal() as db:
                barrier.wait()
                return manual_allocate_payment(
                    db,
                    cid,
                    race_payment,
                    race_order,
                    dec("25.00"),
                    __import__("datetime").date(2026, 7, 16),
                    "manual-race-a3",
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: race(), range(2)))
        assert results[0] == results[1]
        with SessionLocal() as db:
            assert db.execute(
                text(
                    """SELECT COUNT(*) FROM payment_allocations
                    WHERE company_id=:cid AND payment_id=:payment_id"""
                ),
                {"cid": cid, "payment_id": race_payment},
            ).scalar_one() == 1

print("PAYMENT_ALLOCATION_API_OK")
'''


_DISABLED_SMOKE = r'''
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app


with TestClient(app) as client:
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
            "new_password": "AllocationApiDisabled123!",
        },
    )
    assert changed.status_code == 200, changed.text
    headers["Authorization"] = "Bearer " + changed.json()["access_token"]
    cid = int(headers["X-Company-ID"])
    with SessionLocal() as db:
        customer = int(db.execute(
            text(
                "INSERT INTO customers(name,company_id) "
                "VALUES('A3 Kapalı Cari',:cid) RETURNING id"
            ),
            {"cid": cid},
        ).scalar_one())
        order_id = int(db.execute(
            text(
                """INSERT INTO orders(
                    customer_id,order_date,due_date,final_total,status,paid_amount,
                    payment_term,payment_method,company_id
                ) VALUES(
                    :customer,'2026-01-01','2026-08-01',100,'completed',0,
                    'HARMAN_VADELI','credit',:cid
                ) RETURNING id"""
            ),
            {"customer": customer, "cid": cid},
        ).scalar_one())
        payment_id = int(db.execute(
            text(
                """INSERT INTO payments(
                    entity_type,entity_id,amount,payment_date,company_id,payment_method
                ) VALUES('customer',:customer,10,'2026-07-01',:cid,'cash')
                RETURNING id"""
            ),
            {"customer": customer, "cid": cid},
        ).scalar_one())
        allocation_id = int(db.execute(
            text(
                """INSERT INTO payment_allocations(
                    company_id,payment_id,order_id,amount,allocation_type,effective_date
                ) VALUES(:cid,:payment,:order_id,5,'manual','2026-07-01')
                RETURNING id"""
            ),
            {"cid": cid, "payment": payment_id, "order_id": order_id},
        ).scalar_one())
        db.commit()

    assert client.get(
        f"/api/payment-allocations/payments/{payment_id}",
        headers=headers,
    ).status_code == 200
    assert client.get(
        "/api/payment-allocations/reconciliation",
        headers=headers,
    ).status_code == 200
    legacy_receivable = client.get(
        "/api/receivables",
        headers=headers,
    )
    assert legacy_receivable.status_code == 200, legacy_receivable.text
    legacy_row = next(
        row
        for row in legacy_receivable.json()["receivables"]
        if row["id"] == order_id
    )
    assert legacy_row["outstanding"] == 90.0
    writes = (
        client.post(
            "/api/payment-allocations/manual",
            headers={**headers, "Idempotency-Key": "disabled-manual"},
            json={
                "payment_id": payment_id,
                "order_id": order_id,
                "amount": "1.00",
                "effective_date": "2026-07-16",
            },
        ),
        client.post(
            f"/api/payment-allocations/{allocation_id}/reversal",
            headers={**headers, "Idempotency-Key": "disabled-reversal"},
            json={"amount": "1.00", "effective_date": "2026-07-16"},
        ),
        client.post(
            "/api/payment-allocations/reallocation",
            headers={**headers, "Idempotency-Key": "disabled-reallocation"},
            json={
                "allocation_id": allocation_id,
                "target_order_id": order_id,
                "amount": "1.00",
                "effective_date": "2026-07-16",
            },
        ),
    )
    assert all(response.status_code == 409 for response in writes)
    with SessionLocal() as db:
        assert db.execute(
            text(
                "SELECT COUNT(*) FROM payment_allocations "
                "WHERE company_id=:cid AND payment_id=:payment"
            ),
            {"cid": cid, "payment": payment_id},
        ).scalar_one() == 1

print("PAYMENT_ALLOCATION_API_FLAG_OFF_OK")
'''
