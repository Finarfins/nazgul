"""PostgreSQL twin of the FAZ-2 labor-line suite, plus the approval race.

The smoke runs unchanged against real PostgreSQL 16 so the dialect-sensitive
parts get true parity coverage (NUMERIC bounds, RETURNING inserts, the
FOR UPDATE locks, the CHECK constraint on line_status).

``test_concurrent_approval_has_a_single_winner`` additionally drives two real
concurrent connections at the same DRAFT line: the compare-and-set
(``UPDATE ... WHERE line_status='DRAFT'``) must let exactly one through, so an
approved line can never end up with a second approver overwriting the first —
and, because approval is what promotes a line into the billing source, the
billed amount cannot be doubled by a duplicate approval.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest

from test_servis_v2_faz2_labor import run_servis_v2_faz2_labor_smoke

# Both tests in this file share one PostgreSQL database, and the smoke rotates
# the bootstrap password. Using the SAME password it settles on keeps the race
# test's login working whichever test runs first.
ADMIN_PW = "ServisFaz2!123"
ROUNDS = 12


def _database_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("APP_TEST_DATABASE_URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_servis_v2_faz2_labor_postgresql() -> None:
    run_servis_v2_faz2_labor_smoke(_database_url())


@pytest.mark.postgresql
def test_concurrent_approval_has_a_single_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", _database_url())

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
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
        else:
            login = client.post(
                "/api/auth/login", json={"username": "admin", "password": ADMIN_PW}
            )
            assert login.status_code == 200, login.text
            body = login.json()
            headers = {
                "Authorization": "Bearer " + body["access_token"],
                "X-Company-ID": str(body["companies"][0]["id"]),
            }
        admin_id = int(body["user"]["id"])

        customer = client.post(
            "/api/customers", headers=headers, json={"name": "Labor Race"}
        )
        assert customer.status_code == 201, customer.text
        machine = client.post(
            "/api/machines",
            headers=headers,
            json={
                "customer_id": customer.json()["id"],
                "brand": "PG",
                "model": "LaborRace",
                "serial_number": "PG-LABOR-RACE",
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
            line = client.post(
                f"/api/work-orders/{work_order_id}/labor-lines",
                headers=headers,
                json={"technician_user_id": admin_id, "hours": "3", "hourly_rate": "150"},
            )
            assert line.status_code == 201, line.text
            line_id = line.json()["id"]

            barrier = Barrier(2)

            def approve() -> int:
                with TestClient(app) as concurrent:
                    barrier.wait()
                    return concurrent.post(
                        f"/api/work-orders/{work_order_id}/labor-lines/{line_id}/approve",
                        headers=headers,
                    ).status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(approve)
                second = pool.submit(approve)
                statuses = sorted([first.result(), second.result()])

            assert statuses == [200, 409], (index, statuses)

            lines = client.get(
                f"/api/work-orders/{work_order_id}/labor-lines", headers=headers
            ).json()
            approved = [
                item for item in lines["items"] if item["line_status"] == "APPROVED"
            ]
            assert len(approved) == 1, (index, lines)
            # One approval, one billable amount: the line is counted once.
            assert Decimal(str(lines["approved_total"])) == Decimal("450.00"), lines

            for status in ("IN_PROGRESS", "COMPLETED"):
                moved = client.patch(
                    f"/api/work-orders/{work_order_id}/status",
                    headers=headers,
                    json={"status": status},
                )
                assert moved.status_code == 200, moved.text
            summary = client.get(
                f"/api/work-orders/{work_order_id}/invoice", headers=headers
            ).json()
            assert summary["totals"]["labor_source"] == "lines", summary
            # 450 from the single approved line — never 450 + the 200 header.
            assert Decimal(str(summary["totals"]["labor"])) == Decimal("450.00"), summary
