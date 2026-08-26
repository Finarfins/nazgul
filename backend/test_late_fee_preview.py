from __future__ import annotations

from datetime import date
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.auth import required_permission
from app.late_fee_engine import calculate_late_fee
from app.receivables_engine import PrincipalChange


BACKEND = Path(__file__).resolve().parent


def change(day: int, amount: str, source: str = "payment") -> PrincipalChange:
    return PrincipalChange(date(2026, 1, day), Decimal(amount), source, day)


def test_time_slices_payment_on_its_effective_day() -> None:
    result = calculate_late_fee(
        original_principal="1000",
        due_date=date(2026, 1, 1),
        as_of=date(2026, 1, 10),
        annual_rate="73",
        changes=(change(6, "-400"),),
    )
    assert result.amount == Decimal("14.00")
    assert [
        (row.start_date, row.end_date, row.days, row.principal)
        for row in result.segments
    ] == [
        (date(2026, 1, 2), date(2026, 1, 5), 4, Decimal("1000")),
        (date(2026, 1, 6), date(2026, 1, 10), 5, Decimal("600")),
    ]


def test_reversal_increases_principal_on_its_effective_day() -> None:
    result = calculate_late_fee(
        original_principal="1000",
        due_date=date(2026, 1, 1),
        as_of=date(2026, 1, 10),
        annual_rate="73",
        changes=(
            change(6, "-400", "allocation"),
            change(8, "100", "allocation_reversal"),
        ),
    )
    assert result.amount == Decimal("14.60")
    assert [row.principal for row in result.segments] == [
        Decimal("1000"),
        Decimal("600"),
        Decimal("700"),
    ]


def test_grace_boundary_and_zero_rate() -> None:
    grace_end = calculate_late_fee(
        original_principal="1000",
        due_date=date(2026, 1, 1),
        as_of=date(2026, 1, 6),
        annual_rate="36.5",
        grace_days=5,
    )
    first_interest_day = calculate_late_fee(
        original_principal="1000",
        due_date=date(2026, 1, 1),
        as_of=date(2026, 1, 7),
        annual_rate="36.5",
        grace_days=5,
    )
    zero_rate = calculate_late_fee(
        original_principal="1000",
        due_date=date(2026, 1, 1),
        as_of=date(2026, 2, 1),
        annual_rate="0",
    )
    assert grace_end.amount == Decimal("0.00")
    assert grace_end.segments == ()
    assert first_interest_day.amount == Decimal("1.00")
    assert first_interest_day.segments[0].days == 1
    assert zero_rate.amount == Decimal("0.00")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"original_principal": "-0.01", "annual_rate": "10", "grace_days": 0},
        {"original_principal": "1", "annual_rate": "-0.01", "grace_days": 0},
        {"original_principal": "1", "annual_rate": "10", "grace_days": -1},
    ],
)
def test_negative_inputs_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        calculate_late_fee(
            due_date=date(2026, 1, 1),
            as_of=date(2026, 1, 2),
            **kwargs,
        )


def test_intermediate_segments_are_not_rounded_and_interest_is_not_compounded() -> None:
    result = calculate_late_fee(
        original_principal="1",
        due_date=date(2026, 1, 1),
        as_of=date(2026, 1, 11),
        annual_rate="36.5",
    )
    # Each day's 0.001 contribution would disappear if rounded per day.
    assert result.segments[0].unrounded_interest == Decimal("0.010")
    assert result.amount == Decimal("0.01")
    # A prior preview amount is output only; it is never fed back into principal.
    repeated = calculate_late_fee(
        original_principal="1",
        due_date=date(2026, 1, 1),
        as_of=date(2026, 1, 11),
        annual_rate="36.5",
    )
    assert repeated.amount == result.amount


def test_late_fee_preview_uses_finance_permission() -> None:
    assert (
        required_permission("GET", "/api/finance/late-fees/preview")
        == "finance"
    )


def run_late_fee_preview_smoke(database_url: str) -> None:
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


def test_late_fee_preview_sqlite(tmp_path: Path) -> None:
    run_late_fee_preview_smoke(
        f"sqlite:///{(tmp_path / 'late-fee-preview.db').as_posix()}"
    )


_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.main import app


def login(client, username, password, new_password=None):
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    headers = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(body["companies"][0]["id"]),
    }
    if new_password:
        changed = client.post(
            "/api/auth/change-password",
            headers=headers,
            json={"current_password": password, "new_password": new_password},
        )
        assert changed.status_code == 200, changed.text
        headers["Authorization"] = "Bearer " + changed.json()["access_token"]
    return headers


def create_user(client, headers, username, role):
    initial = "LateFeeUser123!"
    created = client.post(
        "/api/users",
        headers=headers,
        json={
            "username": username,
            "display_name": username,
            "password": initial,
            "role": role,
        },
    )
    assert created.status_code in (200, 201), created.text
    return login(client, username, initial, "LateFeeUser456!")


def insert_order(db, cid, customer_id, document_no, total="1000"):
    return int(db.execute(
        text(
            """INSERT INTO orders(
                customer_id,order_date,due_date,final_total,status,paid_amount,
                payment_term,payment_method,company_id,document_no
            ) VALUES(
                :customer,'2025-12-01','2026-01-01',:total,'completed',0,
                'HARMAN_VADELI','credit',:cid,:document_no
            ) RETURNING id"""
        ),
        {
            "customer": customer_id,
            "total": total,
            "cid": cid,
            "document_no": document_no,
        },
    ).scalar_one())


with TestClient(app) as client:
    admin_headers = login(
        client, "admin", "admin123", "LateFeePreviewAdmin123!"
    )
    cid = int(admin_headers["X-Company-ID"])
    finance_headers = create_user(
        client, admin_headers, "late_fee_finance", "muhasebe"
    )
    sales_headers = create_user(
        client, admin_headers, "late_fee_sales", "satis"
    )
    with SessionLocal() as db:
        customer_id = int(db.execute(
            text(
                "INSERT INTO customers(name,company_id) "
                "VALUES('Vade Farkı Cari',:cid) RETURNING id"
            ),
            {"cid": cid},
        ).scalar_one())
        order_id = insert_order(db, cid, customer_id, "VF-001")
        company_policy_customer_id = int(db.execute(
            text(
                "INSERT INTO customers(name,company_id) "
                "VALUES('Şirket Politikası Cari',:cid) RETURNING id"
            ),
            {"cid": cid},
        ).scalar_one())
        company_policy_order_id = insert_order(
            db,
            cid,
            company_policy_customer_id,
            "VF-002",
            total="100",
        )
        payment_id = int(db.execute(
            text(
                """INSERT INTO payments(
                    entity_type,entity_id,amount,payment_date,company_id,
                    payment_method,reference_type,reference_id
                ) VALUES(
                    'customer',:customer,400,'2026-01-06',:cid,
                    'cash','order',:order_id
                ) RETURNING id"""
            ),
            {"customer": customer_id, "cid": cid, "order_id": order_id},
        ).scalar_one())
        allocation_id = int(db.execute(
            text(
                """INSERT INTO payment_allocations(
                    company_id,payment_id,order_id,amount,allocation_type,effective_date
                ) VALUES(
                    :cid,:payment_id,:order_id,400,'document_create','2026-01-06'
                ) RETURNING id"""
            ),
            {"cid": cid, "payment_id": payment_id, "order_id": order_id},
        ).scalar_one())
        db.execute(
            text(
                """INSERT INTO payment_allocations(
                    company_id,payment_id,order_id,amount,allocation_type,effective_date,
                    reversal_of_allocation_id
                ) VALUES(
                    :cid,:payment_id,:order_id,100,'document_create','2026-01-08',
                    :allocation_id
                )"""
            ),
            {
                "cid": cid,
                "payment_id": payment_id,
                "order_id": order_id,
                "allocation_id": allocation_id,
            },
        )
        db.execute(
            text(
                """INSERT INTO late_fee_policies(
                    company_id,customer_id,annual_rate,day_count_basis,grace_days,
                    tax_mode,vat_rate,effective_from,active
                ) VALUES(
                    :cid,NULL,36.5,365,0,'NO_VAT',0,'2025-01-01',:active
                )"""
            ),
            {"cid": cid, "active": True},
        )
        db.execute(
            text(
                """INSERT INTO late_fee_policies(
                    company_id,customer_id,annual_rate,day_count_basis,grace_days,
                    tax_mode,vat_rate,effective_from,active
                ) VALUES(
                    :cid,:customer,73,365,0,'NO_VAT',0,'2025-01-01',:active
                )"""
            ),
            {"cid": cid, "customer": customer_id, "active": True},
        )
        db.commit()

    denied = client.get(
        "/api/finance/late-fees/preview",
        headers=sales_headers,
        params={"order_id": order_id, "as_of": "2026-01-10"},
    )
    assert denied.status_code == 403, denied.text

    with SessionLocal() as db:
        counts_before = tuple(db.execute(text(query)).scalar_one() for query in (
            "SELECT COUNT(*) FROM late_fee_policies",
            "SELECT COUNT(*) FROM payments",
            "SELECT COUNT(*) FROM payment_allocations",
        ))

    preview = client.get(
        "/api/finance/late-fees/preview",
        headers=finance_headers,
        params={"order_id": order_id, "as_of": "2026-01-10"},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["customer_id"] == customer_id
    assert body["total_late_fee_gross"] == "14.60"
    assert len(body["orders"]) == 1
    row = body["orders"][0]
    assert row["principal_at_interest_start"] == "1000.00"
    assert row["principal_as_of"] == "700.00"
    assert row["late_fee_net"] == row["late_fee_gross"] == "14.60"
    assert row["late_fee_vat"] == "0.00"
    assert row["policy"]["source"] == "customer"
    assert row["policy"]["annual_rate"] == "73.0000"
    assert [
        (segment["days"], segment["principal"])
        for segment in row["segments"]
    ] == [(4, "1000.00"), (2, "600.00"), (3, "700.00")]

    customer_preview = client.get(
        "/api/finance/late-fees/preview",
        headers=finance_headers,
        params={"customer_id": customer_id, "as_of": "2026-01-10"},
    )
    assert customer_preview.status_code == 200, customer_preview.text
    assert customer_preview.json()["total_late_fee_gross"] == "14.60"

    company_policy_preview = client.get(
        "/api/finance/late-fees/preview",
        headers=finance_headers,
        params={"order_id": company_policy_order_id, "as_of": "2026-01-10"},
    )
    assert company_policy_preview.status_code == 200, company_policy_preview.text
    assert (
        company_policy_preview.json()["orders"][0]["policy"]["source"]
        == "company"
    )
    assert (
        company_policy_preview.json()["orders"][0]["policy"]["annual_rate"]
        == "36.5000"
    )

    invalid_selector = client.get(
        "/api/finance/late-fees/preview",
        headers=finance_headers,
        params={
            "order_id": order_id,
            "customer_id": customer_id,
            "as_of": "2026-01-10",
        },
    )
    assert invalid_selector.status_code == 422, invalid_selector.text

    other_company = client.post(
        "/api/companies",
        headers=admin_headers,
        json={"name": "Vade Farkı İzole Şirket"},
    )
    assert other_company.status_code in (200, 201), other_company.text
    other_headers = {
        **admin_headers,
        "X-Company-ID": str(other_company.json()["id"]),
    }
    isolated = client.get(
        "/api/finance/late-fees/preview",
        headers=other_headers,
        params={"order_id": order_id, "as_of": "2026-01-10"},
    )
    assert isolated.status_code == 404, isolated.text

    with SessionLocal() as db:
        counts_after = tuple(db.execute(text(query)).scalar_one() for query in (
            "SELECT COUNT(*) FROM late_fee_policies",
            "SELECT COUNT(*) FROM payments",
            "SELECT COUNT(*) FROM payment_allocations",
        ))
        assert counts_after == counts_before
        try:
            db.execute(
                text(
                    """INSERT INTO late_fee_policies(
                        company_id,annual_rate,day_count_basis,grace_days,tax_mode,
                        vat_rate,effective_from,active
                    ) VALUES(:cid,-1,365,0,'NO_VAT',0,'2026-01-01',:active)"""
                ),
                {"cid": cid, "active": True},
            )
            db.commit()
            raise AssertionError("negative annual_rate unexpectedly accepted")
        except IntegrityError:
            db.rollback()
'''
