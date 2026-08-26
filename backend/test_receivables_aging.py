from __future__ import annotations

import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from app.auth import required_permission
from app.routers.reports import _aging_bucket


BACKEND = Path(__file__).resolve().parent


@pytest.mark.parametrize(
    ("days_overdue", "expected"),
    [
        (-1, "not_due"),
        (0, "not_due"),
        (1, "days_1_30"),
        (30, "days_1_30"),
        (31, "days_31_60"),
        (60, "days_31_60"),
        (61, "days_61_90"),
        (90, "days_61_90"),
        (91, "days_90_plus"),
    ],
)
def test_aging_bucket_boundaries(days_overdue: int, expected: str) -> None:
    as_of = date(2026, 7, 26)
    due_date = date.fromordinal(as_of.toordinal() - days_overdue)
    assert _aging_bucket(due_date, as_of) == expected


def test_receivables_aging_uses_reports_permission() -> None:
    assert (
        required_permission("GET", "/api/reports/receivables-aging")
        == "reports"
    )


def run_receivables_aging_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_receivables_aging_endpoint_is_tenant_scoped(tmp_path: Path) -> None:
    database = tmp_path / "receivables-aging.db"
    run_receivables_aging_smoke(f"sqlite:///{database.as_posix()}")


_SMOKE = r'''
from decimal import Decimal
from fastapi.testclient import TestClient
from app.business_time import business_today
from app.main import app
from app.db import SessionLocal
from sqlalchemy import text


def amount(value):
    return Decimal(str(value))


with TestClient(app) as client:
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin123"},
    ).json()
    headers = {
        "Authorization": "Bearer " + login["access_token"],
        "X-Company-ID": str(login["companies"][0]["id"]),
    }
    changed = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={
            "current_password": "admin123",
            "new_password": "ReceivableAging123!",
        },
    )
    assert changed.status_code == 200, changed.text
    headers["Authorization"] = "Bearer " + changed.json()["access_token"]

    company_a = client.post(
        "/api/companies", headers=headers, json={"name": "Yaşlandırma A"}
    ).json()["id"]
    headers["X-Company-ID"] = str(company_a)
    customer_a = client.post(
        "/api/customers", headers=headers, json={"name": "Müşteri A"}
    ).json()["id"]
    customer_b = client.post(
        "/api/customers", headers=headers, json={"name": "Müşteri B"}
    ).json()["id"]
    product = client.post(
        "/api/products",
        headers=headers,
        json={"name": "Yaşlandırma Ürünü", "sale_price": "100", "stock": "1000"},
    ).json()["id"]

    def sale(
        customer_id,
        due_date,
        paid="0",
        status="completed",
        payment_term="HARMAN_VADELI",
        transaction_date="2026-01-01",
    ):
        response = client.post(
            "/api/orders",
            headers=headers,
            json={
                "entity_id": customer_id,
                "transaction_date": transaction_date,
                "due_date": due_date,
                "payment_term": payment_term,
                "status": status,
                "payment_method": "cash",
                "paid_amount": paid,
                "items": [
                    {
                        "product_id": product,
                        "quantity": "1",
                        "unit_price": "100",
                        "vat_rate": 20,
                    }
                ],
            },
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    future_id = sale(customer_a, "2026-08-05")
    sale(customer_a, "2026-07-26")
    sale(customer_a, "2026-06-26")
    sale(customer_a, "2026-06-25")
    sale(customer_a, "2026-05-27")
    sale(customer_a, "2026-05-26")
    sale(customer_a, "2026-04-27")
    sale(customer_a, "2026-04-26")
    sale(customer_b, "2026-07-25", paid="25")
    sale(customer_a, "2026-07-01", status="draft")
    sale(customer_a, "2026-07-01", status="cancelled")
    sale(customer_a, "2026-07-01", paid="100")
    non_harman_id = sale(
        customer_a,
        "2026-07-20",
        payment_term="PESIN",
    )
    invalid_due_id = sale(customer_a, "2026-07-15")
    with SessionLocal() as db:
        db.execute(
            text(
                "UPDATE orders SET due_date=:due_date "
                "WHERE id=:id AND company_id=:cid"
            ),
            {
                "due_date": "2026-99-99",
                "id": invalid_due_id,
                "cid": company_a,
            },
        )
        db.commit()

    report = client.get(
        "/api/reports/receivables-aging",
        headers=headers,
        params={"as_of": "2026-07-26"},
    )
    assert report.status_code == 200, report.text
    data = report.json()
    assert data["as_of"] == "2026-07-26"
    assert data["totals"] == {
        "not_due": "200.00",
        "days_1_30": "275.00",
        "days_31_60": "200.00",
        "days_61_90": "200.00",
        "days_90_plus": "100.00",
        "total": "975.00",
    }
    by_customer = {row["customer_name"]: row for row in data["customers"]}
    assert by_customer["Müşteri A"]["total"] == "900.00"
    assert any(
        document["id"] == non_harman_id
        for document in by_customer["Müşteri A"]["documents"]
    )
    assert all(
        document["id"] != invalid_due_id
        for customer in data["customers"]
        for document in customer["documents"]
    )
    assert sum(
        amount(by_customer["Müşteri A"][bucket])
        for bucket in (
            "not_due",
            "days_1_30",
            "days_31_60",
            "days_61_90",
            "days_90_plus",
        )
    ) == amount(by_customer["Müşteri A"]["total"])
    assert sum(amount(row["total"]) for row in data["customers"]) == amount(
        data["totals"]["total"]
    )
    with SessionLocal() as db:
        all_valid_due_outstanding = amount(
            db.execute(
                text(
                    """SELECT COALESCE(SUM(final_total-paid_amount),0)
                    FROM orders
                    WHERE company_id=:cid
                      AND due_date IS NOT NULL AND due_date<>''
                      AND due_date<>:invalid_due
                      AND COALESCE(status,'completed')
                          NOT IN ('draft','cancelled')
                      AND final_total-paid_amount>0"""
                ),
                {"cid": company_a, "invalid_due": "2026-99-99"},
            ).scalar_one()
        )
    assert amount(data["totals"]["total"]) - all_valid_due_outstanding == Decimal(
        "0.00"
    )
    assert any(
        document["id"] == future_id
        and document["remaining"] == "100.00"
        for document in by_customer["Müşteri A"]["documents"]
    )

    later = client.get(
        "/api/reports/receivables-aging",
        headers=headers,
        params={"as_of": "2026-08-26"},
    ).json()
    assert later["totals"]["not_due"] == "0.00"
    assert later["totals"]["total"] == data["totals"]["total"]

    company_b = client.post(
        "/api/companies", headers=headers, json={"name": "Yaşlandırma B"}
    ).json()["id"]
    headers_b = {**headers, "X-Company-ID": str(company_b)}
    isolated = client.get(
        "/api/reports/receivables-aging",
        headers=headers_b,
        params={"as_of": "2026-07-26"},
    )
    assert isolated.status_code == 200, isolated.text
    assert isolated.json()["customers"] == []
    assert isolated.json()["totals"]["total"] == "0.00"

    historical_company = client.post(
        "/api/companies", headers=headers, json={"name": "Tarihsel Yaşlandırma"}
    ).json()["id"]
    historical_headers = {
        **headers,
        "X-Company-ID": str(historical_company),
    }
    linked_customer = client.post(
        "/api/customers",
        headers=historical_headers,
        json={"name": "Bağlı Tahsilat"},
    ).json()["id"]
    fifo_customer = client.post(
        "/api/customers",
        headers=historical_headers,
        json={"name": "FIFO Tahsilat"},
    ).json()["id"]
    overpaid_customer = client.post(
        "/api/customers",
        headers=historical_headers,
        json={"name": "Fazla Tahsilat"},
    ).json()["id"]
    return_customer = client.post(
        "/api/customers",
        headers=historical_headers,
        json={"name": "Tarihsel İade"},
    ).json()["id"]
    synced_customer = client.post(
        "/api/customers",
        headers=historical_headers,
        json={"name": "Senkron Bağlı Ödeme"},
    ).json()["id"]
    unlinked_customer = client.post(
        "/api/customers",
        headers=historical_headers,
        json={"name": "Tek Bağlantısız Ödeme"},
    ).json()["id"]
    snapshot_customer = client.post(
        "/api/customers",
        headers=historical_headers,
        json={"name": "Yalnız Paid Amount"},
    ).json()["id"]
    historical_product = client.post(
        "/api/products",
        headers=historical_headers,
        json={"name": "Tarihsel Ürün", "sale_price": "100", "stock": "1000"},
    ).json()["id"]
    product = historical_product
    headers = historical_headers

    linked_order = sale(
        linked_customer,
        "2026-06-01",
        transaction_date="2026-05-01",
    )
    future_order = sale(
        linked_customer,
        "2026-07-20",
        transaction_date="2026-07-05",
    )
    fifo_old = sale(
        fifo_customer,
        "2026-05-01",
        transaction_date="2026-04-01",
    )
    fifo_new = sale(
        fifo_customer,
        "2026-06-10",
        transaction_date="2026-04-01",
    )
    overpaid_orders = [
        sale(
            overpaid_customer,
            due_date,
            transaction_date="2026-04-01",
        )
        for due_date in ("2026-04-01", "2026-05-01")
    ]
    returned_before = sale(
        return_customer,
        "2026-04-15",
        transaction_date="2026-03-01",
    )
    returned_after = sale(
        return_customer,
        "2026-05-01",
        transaction_date="2026-03-01",
    )
    synced_order = sale(
        synced_customer,
        "2026-07-10",
        paid="25",
        transaction_date="2026-07-01",
    )
    unlinked_order = sale(
        unlinked_customer,
        "2026-07-11",
        transaction_date="2026-07-01",
    )
    snapshot_only_order = sale(
        snapshot_customer,
        "2026-07-12",
        transaction_date="2026-07-01",
    )

    with SessionLocal() as db:
        payment_sql = text(
            """INSERT INTO payments(
                entity_type,entity_id,amount,payment_date,company_id,
                payment_method,reference_type,reference_id
            ) VALUES(
                'customer',:entity_id,:amount,:payment_date,:cid,
                'cash',:reference_type,:reference_id
            )"""
        )
        db.execute(
            payment_sql,
            {
                "entity_id": linked_customer,
                "amount": "100",
                "payment_date": "2026-07-10",
                "cid": historical_company,
                "reference_type": "order",
                "reference_id": linked_order,
            },
        )
        # Bugünkü paid_amount snapshot'ı gelecekteki bağlı ödemeyi içerir; geçmiş
        # as_of bunu kayıtsız residual sanıp erken uygulamamalıdır.
        db.execute(
            text(
                """UPDATE orders SET paid_amount=:paid
                WHERE id=:id AND company_id=:cid"""
            ),
            {
                "paid": "100",
                "id": linked_order,
                "cid": historical_company,
            },
        )
        db.execute(
            payment_sql,
            {
                "entity_id": fifo_customer,
                "amount": "120",
                "payment_date": "2026-06-15",
                "cid": historical_company,
                "reference_type": None,
                "reference_id": None,
            },
        )
        db.execute(
            payment_sql,
            {
                "entity_id": overpaid_customer,
                "amount": "250",
                "payment_date": "2026-06-15",
                "cid": historical_company,
                "reference_type": None,
                "reference_id": None,
            },
        )
        db.execute(
            payment_sql,
            {
                "entity_id": unlinked_customer,
                "amount": "30",
                "payment_date": "2026-07-15",
                "cid": historical_company,
                "reference_type": None,
                "reference_id": None,
            },
        )
        db.execute(
            text(
                """UPDATE orders SET paid_amount=:paid
                WHERE id=:id AND company_id=:cid"""
            ),
            {
                "paid": "40",
                "id": snapshot_only_order,
                "cid": historical_company,
            },
        )
        return_sql = text(
            """INSERT INTO returns(
                return_type,entity_id,return_date,total,company_id,status,
                source_type,source_id
            ) VALUES(
                'sale_return',:entity_id,:return_date,:total,:cid,'completed',
                :source_type,:source_id
            )"""
        )
        db.execute(
            return_sql,
            {
                "entity_id": fifo_customer,
                "return_date": "2026-06-20",
                "total": "30",
                "cid": historical_company,
                "source_type": None,
                "source_id": None,
            },
        )
        db.execute(
            return_sql,
            {
                "entity_id": fifo_customer,
                "return_date": "2026-07-01",
                "total": "40",
                "cid": historical_company,
                "source_type": None,
                "source_id": None,
            },
        )
        for source_id, return_date in (
            (returned_before, "2026-06-20"),
            (returned_after, "2026-07-01"),
        ):
            db.execute(
                return_sql,
                {
                    "entity_id": return_customer,
                    "return_date": return_date,
                    "total": "100",
                    "cid": historical_company,
                    "source_type": "order",
                    "source_id": source_id,
                },
            )
        db.commit()

    historical = client.get(
        "/api/reports/receivables-aging",
        headers=historical_headers,
        params={"as_of": "2026-06-30"},
    )
    assert historical.status_code == 200, historical.text
    historical_data = historical.json()
    historical_customers = {
        row["customer_name"]: row for row in historical_data["customers"]
    }
    assert historical_data["totals"]["total"] == "250.00"
    assert historical_data["totals"]["days_1_30"] == "150.00"
    assert historical_data["totals"]["days_31_60"] == "100.00"
    assert "Fazla Tahsilat" not in historical_customers
    assert all(
        amount(row[bucket]) >= 0
        for row in historical_data["customers"]
        for bucket in (
            "not_due",
            "days_1_30",
            "days_31_60",
            "days_61_90",
            "days_90_plus",
        )
    )
    linked_documents = historical_customers["Bağlı Tahsilat"]["documents"]
    assert len(linked_documents) == 1
    assert linked_documents[0]["id"] == linked_order
    assert linked_documents[0]["due_date"] == "2026-06-01"
    assert linked_documents[0]["remaining"] == "100.00"
    assert all(
        document["id"] != future_order
        for row in historical_data["customers"]
        for document in row["documents"]
    )
    fifo_documents = historical_customers["FIFO Tahsilat"]["documents"]
    assert [document["id"] for document in fifo_documents] == [fifo_new]
    assert fifo_documents[0]["remaining"] == "50.00"
    assert all(
        document["id"] != fifo_old
        for document in historical_customers["FIFO Tahsilat"]["documents"]
    )
    assert all(
        document["id"] not in overpaid_orders
        for row in historical_data["customers"]
        for document in row["documents"]
    )
    return_documents = historical_customers["Tarihsel İade"]["documents"]
    assert [document["id"] for document in return_documents] == [returned_after]
    assert return_documents[0]["remaining"] == "100.00"

    company_a_headers = {**headers, "X-Company-ID": str(company_a)}
    default_today = client.get(
        "/api/reports/receivables-aging", headers=company_a_headers
    )
    explicit_today = client.get(
        "/api/reports/receivables-aging",
        headers=company_a_headers,
        params={"as_of": business_today().isoformat()},
    )
    assert default_today.status_code == 200, default_today.text
    assert default_today.json() == explicit_today.json()

    historical_today = client.get(
        "/api/reports/receivables-aging",
        headers=historical_headers,
    )
    assert historical_today.status_code == 200, historical_today.text
    remaining_by_order = {
        document["id"]: amount(document["remaining"])
        for customer in historical_today.json()["customers"]
        for document in customer["documents"]
    }
    comparisons = {
        "linked_payment": [synced_order],
        "unlinked_payment": [unlinked_order],
        "paid_amount_without_payment": [snapshot_only_order],
        "linked_return": [returned_before],
        "mixed_customer_fifo": [fifo_old, fifo_new],
    }
    # Bunlar eski snapshot'la eşitlik beklentisi değil, hareketlerden hesaplanan
    # gerçek kalan borçlardır: cari tahsilatlar ve iadeler bugün de düşülür.
    actual_remaining = {}
    for case, order_ids in comparisons.items():
        actual_remaining[case] = sum(
            (remaining_by_order.get(order_id, amount(0)) for order_id in order_ids),
            amount(0),
        )
    assert actual_remaining == {
        "linked_payment": amount(75),
        "unlinked_payment": amount(70),
        "paid_amount_without_payment": amount(60),
        "linked_return": amount(0),
        "mixed_customer_fifo": amount(10),
    }
'''
