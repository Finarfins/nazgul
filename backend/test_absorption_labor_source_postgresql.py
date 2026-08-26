"""PostgreSQL twin: absorption labour revenue comes from the billing source.

Runs the SQLite scenario against real PostgreSQL 16 so the dialect-sensitive
parts hold: the grouped LEFT JOIN that classifies invoiced vs uninvoiced work
orders in one statement, PG16 strict GROUP BY, and NUMERIC SUM arithmetic on
``invoice_items.total``.

It also drives the concurrency regression: a cancellation committing from a
separate connection while the report runs must not change the counted total.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest

from test_absorption_labor_source import run_absorption_labor_source_smoke

ADMIN_PW = "AbsorpLabor!123"
ROUNDS = 15
PERIOD = {"date_from": "2026-01-01", "date_to": "2026-12-31", "overhead": "1000"}


def _pg_url() -> str:
    database_url = os.getenv("ABSORPTION_RATE_TEST_DATABASE_URL") or os.getenv(
        "APP_TEST_DATABASE_URL"
    )
    if not database_url:
        pytest.skip("ABSORPTION_RATE_TEST_DATABASE_URL / APP_TEST_DATABASE_URL is required")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The absorption test database URL must point to PostgreSQL")
    return database_url


def _admin(client) -> tuple[dict, int]:
    """Resilient login: the smoke in this file rotates the bootstrap password."""
    login = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    if login.status_code == 200:
        body = login.json()
        headers = {
            "Authorization": "Bearer " + body["access_token"],
            "X-Company-ID": str(body["companies"][0]["id"]),
        }
        changed = client.post(
            "/api/auth/change-password",
            headers=headers,
            json={"current_password": "admin123", "new_password": ADMIN_PW},
        )
        assert changed.status_code == 200, changed.text
        headers["Authorization"] = "Bearer " + changed.json()["access_token"]
        return headers, int(body["user"]["id"])
    login = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW})
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(body["companies"][0]["id"]),
    }
    return headers, int(body["user"]["id"])


def _leave_period(headers: dict, work_order_id: int) -> None:
    """Push a finished work order out of the reporting window.

    Each round must observe exactly one work order, so the previous round's job
    has its ``completed_at`` cleared once its assertions are done.
    """
    from sqlalchemy import text as sql_text

    from app.db import SessionLocal

    with SessionLocal() as session:
        session.execute(
            sql_text(
                "UPDATE work_orders SET completed_at=NULL "
                "WHERE id=:id AND company_id=:cid"
            ),
            {"id": work_order_id, "cid": int(headers["X-Company-ID"])},
        )
        session.commit()


@pytest.mark.postgresql
def test_absorption_labor_source_postgresql() -> None:
    run_absorption_labor_source_smoke(_pg_url())


@pytest.mark.postgresql
def test_report_never_double_counts_while_an_invoice_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Race: cancelling an invoice while the report runs must not move the total.

    The report classifies work orders (invoiced vs not) and reads the invoiced
    labour in ONE statement, so a cancellation committing from another
    connection lands either entirely before that snapshot or entirely after it:

    * before -> the work order is uninvoiced and the resolver bills its approved
      lines: 850;
    * after  -> the work order is invoiced and its LABOR items are billed: 850.

    Either way the answer is 850 and the work order is counted exactly once.
    With the previous two-statement version the cancellation could land
    *between* them, putting the same work order in both halves (1700) or, with
    the reverse interleaving, in neither (0). Both are asserted against.
    """
    monkeypatch.setenv("DATABASE_URL", _pg_url())

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        headers, admin_id = _admin(client)
        # The smoke in this file shares the database, so the race runs inside its
        # own company: the report is tenant-scoped, which keeps each round's
        # expected total exactly one work order.
        company = client.post(
            "/api/companies", headers=headers, json={"name": "Absorpsiyon Yarış Firması"}
        )
        assert company.status_code == 201, company.text
        headers = {**headers, "X-Company-ID": str(company.json()["id"])}
        customer = client.post(
            "/api/customers", headers=headers, json={"name": "Race Müşteri"}
        )
        assert customer.status_code == 201, customer.text
        machine = client.post(
            "/api/machines",
            headers=headers,
            json={
                "customer_id": customer.json()["id"],
                "brand": "PG",
                "model": "AbsorpRace",
                "serial_number": "PG-ABSORP-RACE",
            },
        )
        assert machine.status_code == 201, machine.text
        machine_id = machine.json()["id"]

        for index in range(ROUNDS):
            work_order = client.post(
                "/api/work-orders",
                headers=headers,
                json={
                    "machine_id": machine_id,
                    "technician_id": admin_id,
                    "actual_hours": "2",
                    "labor_rate": "100",
                },
            )
            assert work_order.status_code == 201, work_order.text
            work_order_id = work_order.json()["id"]
            # Approved lines total 850; the v1 header projection is 200, so a
            # source mix-up is immediately visible in the assertions below.
            for hours, rate in (("3", "150"), ("2", "200")):
                line = client.post(
                    f"/api/work-orders/{work_order_id}/labor-lines",
                    headers=headers,
                    json={
                        "technician_user_id": admin_id,
                        "hours": hours,
                        "hourly_rate": rate,
                    },
                )
                assert line.status_code == 201, line.text
                approve = client.post(
                    f"/api/work-orders/{work_order_id}/labor-lines/"
                    f"{line.json()['id']}/approve",
                    headers=headers,
                )
                assert approve.status_code == 200, approve.text
            for status in ("IN_PROGRESS", "COMPLETED"):
                moved = client.patch(
                    f"/api/work-orders/{work_order_id}/status",
                    headers=headers,
                    json={"status": status},
                )
                assert moved.status_code == 200, moved.text
            invoice = client.post(
                "/api/invoices/generate",
                headers=headers,
                json={"work_order_id": work_order_id},
            )
            assert invoice.status_code == 201, invoice.text
            invoice_id = invoice.json()["id"]

            barrier = Barrier(2)

            def run_report() -> dict:
                with TestClient(app) as concurrent:
                    barrier.wait()
                    response = concurrent.get(
                        "/api/analytics/absorption-rate", headers=headers, params=PERIOD
                    )
                    assert response.status_code == 200, response.text
                    return response.json()["service"]

            def cancel_invoice() -> int:
                with TestClient(app) as concurrent:
                    barrier.wait()
                    return concurrent.post(
                        f"/api/invoices/{invoice_id}/cancel",
                        headers=headers,
                        json={"reason": "Rapor yarışı testi"},
                    ).status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                report_future = pool.submit(run_report)
                cancel_future = pool.submit(cancel_invoice)
                service = report_future.result()
                cancel_status = cancel_future.result()

            assert cancel_status == 200, cancel_status
            total = Decimal(str(service["labor_revenue"]))
            invoiced = Decimal(str(service["invoiced_labor_revenue"]))
            uninvoiced = Decimal(str(service["uninvoiced_labor_revenue"]))

            # Counted exactly once: never 0 (dropped) and never 1700 (doubled).
            assert total == Decimal("850.00"), (index, service)
            # The two halves must partition the total, whichever side won.
            assert invoiced + uninvoiced == total, (index, service)
            assert (
                service["invoiced_work_order_count"]
                + service["uninvoiced_work_order_count"]
                == service["work_order_count"]
            ), (index, service)
            assert service["work_order_count"] == 1, (index, service)

            _leave_period(headers, work_order_id)
