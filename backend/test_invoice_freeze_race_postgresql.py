from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest


@pytest.mark.postgresql
def test_labor_update_cannot_interleave_with_invoice_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F3 race: PUT /work-orders/{id} and invoice generation must serialize.

    A work order's money is frozen once a non-cancelled invoice exists. The PUT
    handler runs the billed-check while holding ``FOR UPDATE OF w`` on the work
    order row; invoice generation reads the same row ``FOR SHARE`` before it
    freezes the totals into the invoice. On PostgreSQL those locks are mutually
    exclusive, so the two operations cannot interleave: either the invoice is
    issued first and the concurrent labor edit is rejected with 409, or the
    labor edit commits first and the invoice freezes the updated labor.

    Without the ``FOR UPDATE`` lock on the PUT path the billed-check can observe
    "unbilled" while an invoice is being issued, letting the labor edit commit
    against an invoice frozen at the old labor total. That divergence
    (``invoice.grand_total != work_order.total_labor_cost``) is exactly what
    this test rejects; it fails on the pre-fix code and passes on the fixed
    serialized path.
    """
    url = os.environ.get("INVOICE_FREEZE_RACE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("INVOICE_FREEZE_RACE_TEST_DATABASE_URL is not configured")
    monkeypatch.setenv("DATABASE_URL", url)
    from fastapi.testclient import TestClient
    from app.main import app

    OLD_LABOR = Decimal("100.00")  # actual_hours 1 * labor_rate 100
    NEW_LABOR = Decimal("200.00")  # actual_hours 2 * labor_rate 100

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin123"}
        ).json()
        cid = login["companies"][0]["id"]
        uid = login["user"]["id"]
        h = {"Authorization": "Bearer " + login["access_token"], "X-Company-ID": str(cid)}
        changed = client.post(
            "/api/auth/change-password",
            headers=h,
            json={"current_password": "admin123", "new_password": "FreezeRacePg123!"},
        ).json()
        h["Authorization"] = "Bearer " + changed["access_token"]
        customer = client.post(
            "/api/customers", headers=h, json={"name": "PG Freeze Race"}
        ).json()
        machine = client.post(
            "/api/machines",
            headers=h,
            json={
                "customer_id": customer["id"],
                "brand": "PG",
                "model": "FreezeRace",
                "serial_number": "PG-FREEZE-M",
            },
        ).json()
        base = {
            "machine_id": machine["id"],
            "customer_id": customer["id"],
            "technician_id": uid,
            "actual_hours": "1",
            "labor_rate": "100",
            "warranty_type": "NONE",
            "warranty_percent": "0",
        }

        def make_completed_work_order() -> int:
            wo = client.post("/api/work-orders", headers=h, json=base).json()
            for status in ("IN_PROGRESS", "COMPLETED"):
                assert (
                    client.patch(
                        f"/api/work-orders/{wo['id']}/status",
                        headers=h,
                        json={"status": status},
                    ).status_code
                    == 200
                )
            return wo["id"]

        # Race a fresh work order many times; a single unlucky interleaving on
        # the pre-fix code is enough to break the freeze invariant below.
        put_won = generate_won = 0
        for _ in range(20):
            order_id = make_completed_work_order()
            barrier = Barrier(2)

            def generate() -> tuple[int, dict]:
                with TestClient(app) as concurrent:
                    barrier.wait()
                    response = concurrent.post(
                        "/api/invoices/generate",
                        headers=h,
                        json={"work_order_id": order_id},
                    )
                    return response.status_code, response.json()

            def update_labor() -> int:
                with TestClient(app) as concurrent:
                    barrier.wait()
                    return concurrent.put(
                        f"/api/work-orders/{order_id}",
                        headers=h,
                        json={**base, "actual_hours": "2", "labor_rate": "100"},
                    ).status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                generate_future = pool.submit(generate)
                put_future = pool.submit(update_labor)
                generate_status, invoice = generate_future.result()
                put_status = put_future.result()

            # The invoice always gets created (it is the work order's only one).
            assert generate_status == 201, invoice
            invoice_grand = Decimal(invoice["totals"]["grand_total"])

            wo = client.get(f"/api/work-orders/{order_id}", headers=h).json()
            wo_labor = Decimal(str(wo["total_labor_cost"]))

            # Core invariant: the frozen invoice total equals the work order's
            # persisted labor. No interleaving may leave them diverged.
            assert invoice_grand == wo_labor, (
                order_id,
                put_status,
                str(invoice_grand),
                str(wo_labor),
            )

            # And the PUT's reported outcome must match persisted state.
            assert put_status in {200, 409}, put_status
            if put_status == 200:
                assert wo_labor == NEW_LABOR, ("labor edit won but not persisted", str(wo_labor))
                put_won += 1
            else:
                assert wo_labor == OLD_LABOR, ("edit rejected but labor changed", str(wo_labor))
                generate_won += 1

    # Both orderings are legal; this only records the contention that occurred.
    assert put_won + generate_won == 20
