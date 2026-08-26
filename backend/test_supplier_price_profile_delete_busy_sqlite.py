from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parent


def test_supplier_price_profile_delete_busy_sqlite(tmp_path: Path) -> None:
    """A busy SQLite write lock must never surface as a 500 -- and never as a lie.

    ``supplier_price_imports`` has no row lock to take on SQLite, so the
    check-then-delete window in ``delete_profile`` stays open: a racing importer
    can hold the single database write lock while the DELETE tries to acquire
    it. The DELETE then fails busy, which arrives as ``OperationalError`` rather
    than ``IntegrityError``.

    Both halves are asserted here, because "not a 500" is only half the
    requirement. SQLITE_BUSY does not mean "this profile is in use", so the
    handler must re-measure on a clean transaction and answer what is actually
    true: 409 only when a committed import really references the profile, 503
    when the collision was nothing but a lock.

    The FK safety net -- the ``IntegrityError`` half -- is covered separately by
    ``test_supplier_price_profile_delete_net.py``, and the PostgreSQL row-lock
    race by ``test_supplier_price_profile_delete_race_postgresql.py``.
    """
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'delete_busy.db').as_posix()}"
    # The race is only reachable in WAL mode with a finite busy timeout, so the
    # test pins both instead of inheriting whatever the ambient .env carries.
    # 300 ms keeps the blocked DELETE from padding the suite; the scenario never
    # depends on the duration, only on the collision happening at all.
    env["SQLITE_BUSY_TIMEOUT_MS"] = "300"
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", SCENARIO],
        env=env,
        capture_output=True,
        text=True,
        cwd=BACKEND,
        timeout=180,
    )
    assert "SUPPLIER_PRICE_PROFILE_DELETE_BUSY_OK" in completed.stdout, (
        completed.stdout + completed.stderr
    )


SCENARIO = r'''
import threading
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import event, text

from app.config import settings
from app.db import SessionLocal, engine
from app.main import app

IN_USE = "Bu profille yapılmış içe aktarımlar var; profil silinemez."
LOCKED = "Kayıt geçici olarak kilitli, lütfen tekrar deneyin."

# The race exists because SQLite serialises every writer on one database lock
# and never blocks readers. Assert that mode instead of trusting the ambient
# configuration: without WAL and a finite busy timeout this test would be
# proving something else.
with engine.connect() as probe:
    assert probe.exec_driver_sql("PRAGMA journal_mode").scalar_one().lower() == "wal"
    assert int(probe.exec_driver_sql("PRAGMA busy_timeout").scalar_one()) == int(
        settings.sqlite_busy_timeout_ms
    )
    assert int(probe.exec_driver_sql("PRAGMA foreign_keys").scalar_one()) == 1


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
        changed = client.post("/api/auth/change-password", headers=headers, json={
            "current_password": password, "new_password": "SupplierBusy456!",
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
        VALUES(:cid,:sid,:pid,'DRY_RUN_READY','busy.xlsx',:sha,1,0,0,0,0,0,:now)"""), {
        "cid": company_id, "sid": supplier_id, "pid": profile_id,
        "sha": sha, "now": datetime.now(timezone.utc),
    })


# --- Choreography ---------------------------------------------------------
# No wall-clock sleeps anywhere: the request thread is parked on real events at
# the two points that decide the outcome -- immediately before the DELETE, and
# immediately before the handler re-measures the cause. The racing importer
# picks its fate in the gap between them, so both cases below are exact rather
# than timing-dependent.
state = {"armed": False}


@event.listens_for(engine, "before_cursor_execute")
def _choreograph(conn, cursor, statement, parameters, context, executemany):
    if not state["armed"]:
        return
    if "DELETE FROM supplier_import_profiles" in statement:
        state["delete_reached"].set()
        assert state["delete_gate"].wait(60), "delete gate never opened"
    elif "COUNT(*) FROM supplier_price_imports" in statement:
        # 1 = the pre-check, 2 = the re-measure the OperationalError branch runs.
        state["counts"] += 1
        if state["counts"] == 2:
            state["recount_reached"].set()
            assert state["recount_gate"].wait(60), "recount gate never opened"


@event.listens_for(engine, "handle_error")
def _note_failure(context):
    if state["armed"] and type(context.original_exception).__name__ == "OperationalError":
        state["delete_failed"].set()


def race(client, headers, company_id, supplier_id, profile_id, *, importer_commits):
    """Drive one full check-then-delete collision and return the DELETE response."""
    state.update(
        armed=True, counts=0,
        delete_reached=threading.Event(), delete_gate=threading.Event(),
        recount_reached=threading.Event(), recount_gate=threading.Event(),
        delete_failed=threading.Event(),
    )
    outcome = {}

    def delete_worker():
        response = client.delete(
            "/api/supplier-prices/profiles/" + str(profile_id), headers=headers
        )
        outcome["status"] = response.status_code
        outcome["body"] = response.text
        outcome["headers"] = dict(response.headers)
        try:
            outcome["detail"] = response.json().get("detail")
        except ValueError:
            outcome["detail"] = None

    worker = threading.Thread(target=delete_worker, daemon=True)
    worker.start()

    # The handler has counted zero imports and is about to issue the DELETE.
    assert state["delete_reached"].wait(60), "DELETE never reached"

    # Only now does the racing importer open its transaction. On SQLite that
    # takes the one database write lock, so the profile is spoken for while
    # staying invisible to the COUNT that already ran.
    writer = SessionLocal()
    insert_import(writer, company_id, supplier_id, profile_id, format(profile_id, "064d"))

    state["delete_gate"].set()
    assert state["delete_failed"].wait(60), (
        "DELETE did not collide with the importer's write lock"
    )

    # The handler is now parked in front of its re-measure. Whatever the
    # importer does here is what a clean transaction is allowed to conclude.
    assert state["recount_reached"].wait(60), (
        "handler never re-measured after the busy failure"
    )
    if importer_commits:
        writer.commit()
    else:
        writer.rollback()
    writer.close()
    state["recount_gate"].set()

    worker.join(timeout=60)
    assert not worker.is_alive(), "DELETE never answered"
    state["armed"] = False
    return outcome


# raise_server_exceptions=False so an unhandled error arrives as the 500 the
# caller would really see. "Never 500" is then an assertion on the status code
# rather than an incidental traceback.
with TestClient(app, raise_server_exceptions=False) as client:
    headers, company_a = login(client)
    supplier = client.post(
        "/api/suppliers", headers=headers, json={"name": "Kilit Tedarikçisi"}
    )
    assert supplier.status_code == 201, supplier.text
    supplier_id = int(supplier.json()["id"])

    def new_profile(name):
        created = client.post(
            "/api/supplier-prices/profiles", headers=headers,
            json=payload(supplier_id, name),
        )
        assert created.status_code == 201, created.text
        return int(created.json()["id"])

    busy_id = new_profile("Kilit Profili")
    in_use_id = new_profile("Kullanımda Profili")

    # --- Case 1: busy, but the profile is NOT in use -------------------------
    # The importer aborted, so nothing references the profile. The only true
    # answer is "try again" -- 409 here would invent a rule violation that never
    # happened.
    result = race(client, headers, company_a, supplier_id, busy_id, importer_commits=False)
    assert result["status"] == 503, (
        "expected 503 for a pure lock collision, got "
        + str(result["status"]) + " " + result["body"]
    )
    assert result["detail"] == LOCKED, result["body"]
    assert result["headers"].get("retry-after") == "1", repr(result["headers"])

    # --- Case 2: busy, and the profile really IS in use ----------------------
    # Identical collision, but this importer committed. The re-measure now finds
    # the child row, so the honest answer is the existing 409.
    result = race(client, headers, company_a, supplier_id, in_use_id, importer_commits=True)
    assert result["status"] == 409, (
        "expected 409 once the racing import committed, got "
        + str(result["status"]) + " " + result["body"]
    )
    assert result["detail"] == IN_USE, result["body"]

    # Neither answer destroyed anything and the session stayed usable.
    for profile_id in (busy_id, in_use_id):
        assert client.get(
            "/api/supplier-prices/profiles/" + str(profile_id), headers=headers
        ).status_code == 200
    assert client.get(
        "/api/supplier-prices/profiles", headers=headers
    ).status_code == 200

print("SUPPLIER_PRICE_PROFILE_DELETE_BUSY_OK")
'''
