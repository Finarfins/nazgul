"""PostgreSQL races for the machine lock protocol (Servis v2 FAZ-1).

A machine's owner is read-for-write by the work-order paths and rewritten by
the ownership paths. Both take ``SELECT ... FOR UPDATE`` on the machines row
first (see the LOCK ORDER note in :mod:`app.routers.machines`), so they
serialize instead of interleaving.

These tests drive real concurrent connections, following the pattern of
``test_invoice_freeze_race_postgresql``. Verified against the pre-fix code:

* (a) FAILS without the lock in ``create_work_order``: a transfer passes its
  "no open work order" check while a concurrent create reads the *old* owner
  unlocked, leaving machine=B with an open work order billed to A;
* (b) FAILS without the lock in the owner-changing ``PUT /machines/{id}``: two
  writers read the same previous owner and append diverging history rows
  (A→B and A→C);
* (c) already passed before the fix — the transfer endpoint held its machine
  lock from the start. It is kept as a regression guard so a later change
  cannot drop that lock unnoticed.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

ADMIN_PW = "MachineLockRace123!"
ROUNDS = 15


def _client_and_headers():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    client.__enter__()
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
        return client, headers, int(body["user"]["id"])
    login = client.post(
        "/api/auth/login", json={"username": "admin", "password": ADMIN_PW}
    )
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(body["companies"][0]["id"]),
    }
    return client, headers, int(body["user"]["id"])


def _require_pg(monkeypatch: pytest.MonkeyPatch) -> None:
    url = os.environ.get("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is not configured")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("APP_TEST_DATABASE_URL must point to PostgreSQL")
    monkeypatch.setenv("DATABASE_URL", url)


def _customer(client, headers, name: str) -> int:
    response = client.post("/api/customers", headers=headers, json={"name": name})
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def _machine(client, headers, owner_id: int, tag: str) -> int:
    response = client.post(
        "/api/machines",
        headers=headers,
        json={
            "customer_id": owner_id,
            "brand": "PG",
            "model": "LockRace",
            "serial_number": f"PG-LOCK-{tag}",
            "chassis_number": f"PG-LOCKC-{tag}",
        },
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def _run_concurrently(first, second):
    """Run two request callables against separate clients, released together."""
    from fastapi.testclient import TestClient

    from app.main import app

    barrier = Barrier(2)

    def wrap(action):
        def run():
            with TestClient(app) as concurrent:
                barrier.wait()
                return action(concurrent)

        return run

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(wrap(first))
        second_future = pool.submit(wrap(second))
        return first_future.result(), second_future.result()


@pytest.mark.postgresql
def test_transfer_and_work_order_create_cannot_interleave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Race (a): a transfer and a work-order create must serialize.

    Legal outcomes are exactly two: the create wins and the transfer is
    rejected with 409 (the machine now has an open work order), or the transfer
    wins and the create adopts the *new* owner. The illegal state this rejects
    is machine owned by B while an open work order is billed to A.
    """
    _require_pg(monkeypatch)
    client, headers, technician_id = _client_and_headers()
    try:
        owner_a = _customer(client, headers, "Lock Race A")
        owner_b = _customer(client, headers, "Lock Race B")

        create_won = transfer_won = 0
        for index in range(ROUNDS):
            machine_id = _machine(client, headers, owner_a, f"A{index}")
            # Ownership starts at A for every round.
            if index:
                reset = client.post(
                    f"/api/machines/{machine_id}/transfer-ownership",
                    headers=headers,
                    json={"to_customer_id": owner_a},
                )
                assert reset.status_code in {200, 409}, reset.text

            def transfer(concurrent):
                response = concurrent.post(
                    f"/api/machines/{machine_id}/transfer-ownership",
                    headers=headers,
                    json={"to_customer_id": owner_b},
                )
                return response.status_code, response.text

            def create_work_order(concurrent):
                # customer_id omitted on purpose: the backend must derive it
                # from the machine's owner under the lock.
                response = concurrent.post(
                    "/api/work-orders",
                    headers=headers,
                    json={
                        "machine_id": machine_id,
                        "technician_id": technician_id,
                        "complaint": "lock race",
                    },
                )
                return response.status_code, response.json()

            (transfer_status, transfer_body), (create_status, created) = _run_concurrently(
                transfer, create_work_order
            )

            machine = client.get(f"/api/machines/{machine_id}", headers=headers).json()
            owner_now = machine["customer_id"]
            open_orders = [
                item
                for item in client.get(
                    "/api/work-orders",
                    headers=headers,
                    params={"machine_id": machine_id, "page_size": 200},
                ).json()["items"]
                if item["status"] not in {"DELIVERED", "CANCELLED"}
            ]

            # THE invariant: every open work order belongs to the machine's
            # current owner. The pre-fix interleaving breaks exactly this.
            for order in open_orders:
                assert order["customer_id"] == owner_now, (
                    "open work order billed to a previous owner",
                    index,
                    order["customer_id"],
                    owner_now,
                    transfer_status,
                    create_status,
                )

            assert create_status in {201, 409}, created
            assert transfer_status in {200, 409}, transfer_body
            if transfer_status == 200:
                # Transfer won: the machine moved and the create either adopted
                # the new owner or was refused.
                assert owner_now == owner_b, (index, owner_now)
                if create_status == 201:
                    assert created["customer_id"] == owner_b, created
                transfer_won += 1
            else:
                # Transfer refused: it can only be because the create won and
                # left an open work order behind.
                assert create_status == 201, (transfer_body, created)
                assert owner_now == owner_a, (index, owner_now)
                assert created["customer_id"] == owner_a, created
                create_won += 1

        # Both orderings are legal; this records the contention that occurred.
        assert create_won + transfer_won == ROUNDS
    finally:
        client.__exit__(None, None, None)


@pytest.mark.postgresql
def test_owner_changing_put_and_transfer_keep_history_chain_consistent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Race (b): the owner-changing card PUT and a transfer must serialize.

    Both append to ``machine_ownership_history``. Serialized, the rows form one
    contiguous chain (A→B→C). Unlocked, both read owner A and write A→B and
    A→C — two rows leaving the same owner, which is the corruption asserted
    against here.
    """
    _require_pg(monkeypatch)
    client, headers, _ = _client_and_headers()
    try:
        owner_a = _customer(client, headers, "Chain A")
        owner_b = _customer(client, headers, "Chain B")
        owner_c = _customer(client, headers, "Chain C")

        for index in range(ROUNDS):
            machine_id = _machine(client, headers, owner_a, f"B{index}")

            def transfer(concurrent):
                return concurrent.post(
                    f"/api/machines/{machine_id}/transfer-ownership",
                    headers=headers,
                    json={"to_customer_id": owner_b},
                ).status_code

            def put_owner(concurrent):
                return concurrent.put(
                    f"/api/machines/{machine_id}",
                    headers=headers,
                    json={
                        "customer_id": owner_c,
                        "brand": "PG",
                        "model": "LockRace",
                        "serial_number": f"PG-LOCK-B{index}",
                        "chassis_number": f"PG-LOCKC-B{index}",
                        "status": "active",
                        "is_active": True,
                    },
                ).status_code

            transfer_status, put_status = _run_concurrently(transfer, put_owner)
            assert transfer_status in {200, 409}, transfer_status
            assert put_status in {200, 409}, put_status

            history = client.get(
                f"/api/machines/{machine_id}/ownership-history", headers=headers
            ).json()
            # The endpoint returns newest first; walk it oldest first.
            chain = list(reversed(history))
            assert chain[0]["from_customer_id"] is None, chain
            assert chain[0]["to_customer_id"] == owner_a, chain

            froms = [row["from_customer_id"] for row in chain[1:]]
            assert len(froms) == len(set(froms)), (
                "two ownership rows leave the same owner — the writers "
                "interleaved on an unlocked read",
                index,
                chain,
            )
            for previous, current in zip(chain, chain[1:]):
                assert current["from_customer_id"] == previous["to_customer_id"], (
                    "ownership history is not a contiguous chain",
                    index,
                    chain,
                )

            machine = client.get(f"/api/machines/{machine_id}", headers=headers).json()
            assert machine["customer_id"] == chain[-1]["to_customer_id"], (
                "machine owner diverged from the last history row",
                index,
                chain,
            )
    finally:
        client.__exit__(None, None, None)


@pytest.mark.postgresql
def test_two_concurrent_transfers_produce_exactly_one_history_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Race (c): two transfers to the same customer — one winner, one row.

    The loser must see the committed new owner and be refused with 409 rather
    than appending a second, duplicate A→B row. Unlike (a) and (b) this already
    held before the lock protocol was formalised, because the transfer endpoint
    always took its machine lock; the test pins that behaviour down.
    """
    _require_pg(monkeypatch)
    client, headers, _ = _client_and_headers()
    try:
        owner_a = _customer(client, headers, "Dup A")
        owner_b = _customer(client, headers, "Dup B")

        for index in range(ROUNDS):
            machine_id = _machine(client, headers, owner_a, f"C{index}")

            def transfer(concurrent):
                return concurrent.post(
                    f"/api/machines/{machine_id}/transfer-ownership",
                    headers=headers,
                    json={"to_customer_id": owner_b},
                ).status_code

            first_status, second_status = _run_concurrently(transfer, transfer)
            assert sorted([first_status, second_status]) == [200, 409], (
                "exactly one transfer may win",
                index,
                first_status,
                second_status,
            )

            history = client.get(
                f"/api/machines/{machine_id}/ownership-history", headers=headers
            ).json()
            moves = [row for row in history if row["from_customer_id"] is not None]
            assert len(moves) == 1, ("duplicate ownership rows", index, history)
            assert moves[0]["from_customer_id"] == owner_a
            assert moves[0]["to_customer_id"] == owner_b

            machine = client.get(f"/api/machines/{machine_id}", headers=headers).json()
            assert machine["customer_id"] == owner_b, (index, machine["customer_id"])
    finally:
        client.__exit__(None, None, None)
