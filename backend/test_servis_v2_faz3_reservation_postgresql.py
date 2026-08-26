"""PostgreSQL twin + concurrency regressions for FAZ-3 part reservations.

The smoke runs unchanged against real PostgreSQL 16. On top of it two races are
driven with genuinely concurrent connections:

* **oversell** — N reservations compete for fewer units than they ask for. The
  guard lives inside the reservation UPDATE, so exactly as many succeed as the
  stock allows and ``reserved_quantity`` never exceeds ``quantity``. A
  SELECT-then-UPDATE implementation lets every caller read the same availability
  and all of them succeed, which is the defect this pins.
* **cancel vs issue** — a cancellation and an issue hit the same RESERVED line
  at once. Both move the line out of RESERVED with a compare-and-set, so exactly
  one wins and the stock ends in the state that winner implies — never both
  (issued *and* returned) and never neither.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest

from test_servis_v2_faz3_reservation import run_servis_v2_faz3_reservation_smoke

ADMIN_PW = "ServisFaz3!123"
ROUNDS = 12


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("APP_TEST_DATABASE_URL must point to PostgreSQL")
    return url


def _admin(client) -> tuple[dict, int]:
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


def _sql(headers: dict, statement: str, params: dict | None = None):
    from sqlalchemy import text as sql_text

    from app.db import SessionLocal

    with SessionLocal() as session:
        result = session.execute(
            sql_text(statement), {"cid": int(headers["X-Company-ID"]), **(params or {})}
        )
        rows = result.mappings().all() if result.returns_rows else []
        session.commit()
    return rows


def _stock(headers: dict, warehouse_id: int, product_id: int) -> tuple[Decimal, Decimal]:
    rows = _sql(
        headers,
        "SELECT quantity,reserved_quantity FROM warehouse_stocks "
        "WHERE company_id=:cid AND warehouse_id=:w AND product_id=:p",
        {"w": warehouse_id, "p": product_id},
    )
    row = rows[0]
    return Decimal(str(row["quantity"])), Decimal(str(row["reserved_quantity"]))


def _prepare(client) -> tuple[dict, int, int, int, int]:
    """Company of its own + a stocked warehouse, machine and technician."""
    headers, admin_id = _admin(client)
    company = client.post(
        "/api/companies", headers=headers, json={"name": "FAZ-3 Yarış Firması"}
    )
    assert company.status_code == 201, company.text
    headers = {**headers, "X-Company-ID": str(company.json()["id"])}
    _sql(headers, "UPDATE companies SET service_parts_mode='RESERVE' WHERE id=:cid")

    warehouses = client.get("/api/warehouses", headers=headers)
    assert warehouses.status_code == 200, warehouses.text
    warehouse_id = warehouses.json()[0]["id"]
    product = client.post(
        "/api/products", headers=headers, json={"name": "Yarış Parça", "price": "100", "stock": "0"}
    )
    assert product.status_code == 201, product.text
    product_id = product.json()["id"]
    customer = client.post("/api/customers", headers=headers, json={"name": "Yarış Müşteri"})
    assert customer.status_code == 201, customer.text
    machine = client.post(
        "/api/machines",
        headers=headers,
        json={
            "customer_id": customer.json()["id"],
            "brand": "PG",
            "model": "Faz3Race",
            "serial_number": "PG-FAZ3-RACE",
        },
    )
    assert machine.status_code == 201, machine.text
    return headers, admin_id, warehouse_id, product_id, machine.json()["id"]


def _seed_stock(headers: dict, warehouse_id: int, product_id: int, amount: str) -> None:
    updated = _sql(
        headers,
        "UPDATE warehouse_stocks SET quantity=:q,reserved_quantity=0 "
        "WHERE company_id=:cid AND warehouse_id=:w AND product_id=:p RETURNING product_id",
        {"q": amount, "w": warehouse_id, "p": product_id},
    )
    if not updated:
        _sql(
            headers,
            "INSERT INTO warehouse_stocks(company_id,warehouse_id,product_id,quantity,"
            "critical_stock,reserved_quantity) VALUES(:cid,:w,:p,:q,0,0)",
            {"q": amount, "w": warehouse_id, "p": product_id},
        )


def _new_work_order(client, headers: dict, machine_id: int, technician_id: int) -> int:
    response = client.post(
        "/api/work-orders",
        headers=headers,
        json={
            "machine_id": machine_id,
            "technician_id": technician_id,
            "actual_hours": "1",
            "labor_rate": "100",
        },
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


@pytest.mark.postgresql
def test_servis_v2_faz3_reservation_postgresql() -> None:
    run_servis_v2_faz3_reservation_smoke(_pg_url())


@pytest.mark.postgresql
def test_concurrent_reservations_never_oversell(monkeypatch: pytest.MonkeyPatch) -> None:
    """Four reservations of 3 units race for 10 units: at most three may win."""
    monkeypatch.setenv("DATABASE_URL", _pg_url())

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        headers, admin_id, warehouse_id, product_id, machine_id = _prepare(client)

        for index in range(ROUNDS):
            _seed_stock(headers, warehouse_id, product_id, "10")
            work_orders = [
                _new_work_order(client, headers, machine_id, admin_id) for _ in range(4)
            ]
            barrier = Barrier(len(work_orders))

            def reserve(work_order_id: int) -> int:
                with TestClient(app) as concurrent:
                    barrier.wait()
                    return concurrent.post(
                        f"/api/work-orders/{work_order_id}/parts",
                        headers=headers,
                        json={
                            "product_id": product_id,
                            "warehouse_id": warehouse_id,
                            "quantity": "3",
                            "unit_price": "100",
                        },
                    ).status_code

            with ThreadPoolExecutor(max_workers=len(work_orders)) as pool:
                statuses = list(pool.map(reserve, work_orders))

            accepted = sum(1 for status in statuses if status == 201)
            rejected = sum(1 for status in statuses if status == 409)
            on_hand, reserved = _stock(headers, warehouse_id, product_id)

            # 10 units / 3 per line -> at most 3 reservations can be honoured.
            assert accepted <= 3, (index, statuses, on_hand, reserved)
            assert accepted + rejected == len(work_orders), (index, statuses)
            # THE invariant: reservations never exceed what is physically there.
            assert reserved == Decimal(accepted) * Decimal("3"), (index, statuses, reserved)
            assert reserved <= on_hand, (index, on_hand, reserved)
            assert on_hand == Decimal("10"), (index, on_hand)  # reserving moves nothing

            # Cancel the round's work orders so the next one starts clean.
            for work_order_id in work_orders:
                client.patch(
                    f"/api/work-orders/{work_order_id}/status",
                    headers=headers,
                    json={"status": "CANCELLED"},
                )


@pytest.mark.postgresql
def test_cancel_and_issue_race_has_a_single_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation and issue hit one RESERVED line: exactly one may act on it."""
    monkeypatch.setenv("DATABASE_URL", _pg_url())

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        headers, admin_id, warehouse_id, product_id, machine_id = _prepare(client)

        for index in range(ROUNDS):
            _seed_stock(headers, warehouse_id, product_id, "10")
            work_order_id = _new_work_order(client, headers, machine_id, admin_id)
            part = client.post(
                f"/api/work-orders/{work_order_id}/parts",
                headers=headers,
                json={
                    "product_id": product_id,
                    "warehouse_id": warehouse_id,
                    "quantity": "4",
                    "unit_price": "100",
                },
            )
            assert part.status_code == 201, part.text
            part_id = part.json()["id"]

            barrier = Barrier(2)

            def issue() -> int:
                with TestClient(app) as concurrent:
                    barrier.wait()
                    return concurrent.post(
                        f"/api/work-orders/{work_order_id}/parts/{part_id}/issue",
                        headers=headers,
                    ).status_code

            def cancel() -> int:
                with TestClient(app) as concurrent:
                    barrier.wait()
                    return concurrent.patch(
                        f"/api/work-orders/{work_order_id}/status",
                        headers=headers,
                        json={"status": "CANCELLED"},
                    ).status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                issue_future = pool.submit(issue)
                cancel_future = pool.submit(cancel)
                issue_status = issue_future.result()
                cancel_status = cancel_future.result()

            on_hand, reserved = _stock(headers, warehouse_id, product_id)
            lines = client.get(
                f"/api/work-orders/{work_order_id}/parts", headers=headers
            ).json()["items"]
            assert len(lines) == 1, (index, lines)
            line_status = lines[0]["line_status"]

            assert issue_status in {200, 409}, (index, issue_status)
            assert cancel_status in {200, 409}, (index, cancel_status)
            # Whoever won, the reservation is gone — it can never be left behind.
            assert reserved == Decimal("0"), (index, issue_status, cancel_status, reserved)

            if line_status == "ISSUED":
                # Issue won and the cancellation could not also return it.
                assert issue_status == 200, (index, issue_status, cancel_status)
                assert on_hand == Decimal("6"), (index, on_hand)
            else:
                # Cancellation won: the line is RETURNED and nothing left stock.
                assert line_status == "RETURNED", (index, line_status)
                assert on_hand == Decimal("10"), (index, on_hand)

            # Whatever happened, stock must never be double-counted.
            assert on_hand in {Decimal("6"), Decimal("10")}, (index, on_hand)
