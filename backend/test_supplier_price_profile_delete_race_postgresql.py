from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parent


@pytest.mark.postgresql
def test_supplier_price_profile_delete_race_postgresql() -> None:
    """DELETE /profiles/{id} must answer 409 -- never 500 -- under a real race.

    PostgreSQL only: the scenario needs two concurrent sessions and genuine row
    locks, neither of which SQLite provides.
    """
    database_url = os.environ.get("SUPPLIER_PRICE_IMPORT_TEST_DATABASE_URL") or os.environ.get(
        "APP_TEST_DATABASE_URL"
    )
    if not database_url:
        pytest.skip("SUPPLIER_PRICE_IMPORT_TEST_DATABASE_URL or APP_TEST_DATABASE_URL is required")

    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", SCENARIO],
        env=env,
        capture_output=True,
        text=True,
        cwd=BACKEND,
        timeout=180,
    )
    assert "SUPPLIER_PRICE_PROFILE_DELETE_RACE_OK" in completed.stdout, (
        completed.stdout + completed.stderr
    )


SCENARIO = r'''
import threading
import time
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app

IN_USE = "Bu profille yapılmış içe aktarımlar var; profil silinemez."


def login(client, username="admin", password="admin123"):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    if response.status_code != 200 and username == "admin":
        password = "SupplierImport123!"
        response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    body = response.json()
    headers = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(body["companies"][0]["id"]),
    }
    if body["user"].get("must_change_password"):
        new_password = "SupplierRace456!"
        changed = client.post("/api/auth/change-password", headers=headers, json={
            "current_password": password, "new_password": new_password,
        })
        assert changed.status_code == 200, changed.text
        headers["Authorization"] = "Bearer " + changed.json()["access_token"]
    return headers, int(body["companies"][0]["id"])


def payload(supplier_id, name):
    return {
        "supplier_id": supplier_id,
        "name": name,
        "source_type": "xlsx",
        "sheet_selector": ".*",
        "header_row_strategy": "detect",
        "column_map": {
            "code": "Kod", "description": "Açıklama",
            "price_cash": "Peşin", "price_term": "Vadeli",
        },
        "currency_mode": "fixed:TRY",
        "term_days": 30,
        "vat_source": "fixed:20",
        "warning_threshold": "25",
        "blocked_threshold": "100",
    }


def insert_import(db, company_id, supplier_id, profile_id, sha):
    db.execute(text("""INSERT INTO supplier_price_imports(
        company_id,supplier_id,profile_id,status,source_filename,source_sha256,
        source_bytes,parsed_row_count,matched_count,unmatched_count,warning_count,
        blocked_count,created_at)
        VALUES(:cid,:sid,:pid,'DRY_RUN_READY','race.xlsx',:sha,1,0,0,0,0,0,:now)"""), {
        "cid": company_id, "sid": supplier_id, "pid": profile_id,
        "sha": sha, "now": datetime.now(timezone.utc),
    })


def new_profile(client, headers, supplier_id, name):
    created = client.post(
        "/api/supplier-prices/profiles", headers=headers, json=payload(supplier_id, name)
    )
    assert created.status_code == 201, created.text
    return int(created.json()["id"])


with TestClient(app) as client:
    headers, company_a = login(client)
    supplier = client.post(
        "/api/suppliers", headers=headers, json={"name": "Yarış Tedarikçisi"}
    )
    assert supplier.status_code == 201, supplier.text
    supplier_id = int(supplier.json()["id"])

    race_id = new_profile(client, headers, supplier_id, "Yarış Profili")
    net_id = new_profile(client, headers, supplier_id, "Ağ Profili")

    # --- Scenario 1: two concurrent sessions, genuine race -------------------
    # Session B opens a transaction and inserts an import against race_id but
    # does NOT commit. The FK on supplier_price_imports.profile_id makes
    # PostgreSQL hold FOR KEY SHARE on that profile row for the whole
    # transaction, so the row is spoken for while staying invisible to any
    # other session's COUNT.
    writer = SessionLocal()
    insert_import(writer, company_a, supplier_id, race_id, "b" * 64)

    outcome = {}

    def delete_worker():
        response = client.delete(
            "/api/supplier-prices/profiles/" + str(race_id), headers=headers
        )
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        outcome["status"] = response.status_code
        outcome["detail"] = detail
        outcome["body"] = response.text
        outcome["finished_at"] = time.monotonic()

    worker = threading.Thread(target=delete_worker, daemon=True)
    worker.start()

    # Session A (the DELETE) must be parked on the row lock while B is still
    # open. Finishing here would mean nothing serialised the two sessions and
    # the check-then-delete window is still wide open.
    time.sleep(3.0)
    assert not outcome, (
        "DELETE did not block on the concurrent import; the profile row was "
        "never locked: " + repr(outcome)
    )

    committed_at = time.monotonic()
    writer.commit()
    writer.close()

    worker.join(timeout=60)
    assert not worker.is_alive(), "DELETE stayed blocked after the writer committed"
    assert outcome["status"] == 409, (
        "expected 409 under the race, got "
        + str(outcome["status"]) + " " + outcome["body"]
    )
    assert outcome["detail"] == IN_USE, outcome["body"]
    assert outcome["finished_at"] >= committed_at, (
        "DELETE answered before the racing insert committed"
    )

    # The racing import kept the profile alive.
    assert client.get(
        "/api/supplier-prices/profiles/" + str(race_id), headers=headers
    ).status_code == 200

    # --- Scenario 2: IntegrityError safety net -------------------------------
    # An import owned by a different company still references net_id. The
    # tenant-scoped COUNT cannot see that row, so the pre-check passes and only
    # the FK stops the DELETE. This is the path the try/except must convert
    # into the same controlled 409 instead of a 500.
    other = client.post(
        "/api/companies", headers=headers, json={"name": "Yarış Şirketi B"}
    )
    assert other.status_code == 201, other.text
    other_id = int(other.json()["id"])
    with SessionLocal() as db:
        insert_import(db, other_id, supplier_id, net_id, "c" * 64)
        db.commit()

    hidden = client.delete(
        "/api/supplier-prices/profiles/" + str(net_id), headers=headers
    )
    assert hidden.status_code == 409, (
        "expected 409 from the FK safety net, got "
        + str(hidden.status_code) + " " + hidden.text
    )
    assert hidden.json()["detail"] == IN_USE, hidden.text

    # The session stayed usable after the rollback.
    assert client.get(
        "/api/supplier-prices/profiles", headers=headers
    ).status_code == 200

print("SUPPLIER_PRICE_PROFILE_DELETE_RACE_OK")
'''
