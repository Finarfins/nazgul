from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parent


def test_supplier_price_profile_delete_integrity_net_sqlite(tmp_path: Path) -> None:
    """The FK safety net must answer 409 even when the pre-check sees nothing.

    The two-session lock race needs PostgreSQL and lives in
    ``test_supplier_price_profile_delete_race_postgresql.py``. This twin covers
    the other half of the fix -- the ``IntegrityError`` handler -- which is what
    carries the guarantee on SQLite, where no row lock exists at all.
    """
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'delete_net.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", SCENARIO],
        env=env,
        capture_output=True,
        text=True,
        cwd=BACKEND,
        timeout=120,
    )
    assert "SUPPLIER_PRICE_PROFILE_DELETE_NET_OK" in completed.stdout, (
        completed.stdout + completed.stderr
    )


SCENARIO = r'''
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
        changed = client.post("/api/auth/change-password", headers=headers, json={
            "current_password": password, "new_password": "SupplierNet456!",
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


with TestClient(app) as client:
    headers, company_a = login(client)
    supplier = client.post("/api/suppliers", headers=headers, json={"name": "Ağ Tedarikçisi"})
    assert supplier.status_code == 201, supplier.text
    supplier_id = int(supplier.json()["id"])

    created = client.post(
        "/api/supplier-prices/profiles", headers=headers,
        json=payload(supplier_id, "Ağ Profili"),
    )
    assert created.status_code == 201, created.text
    profile_id = int(created.json()["id"])

    # An import owned by another company references the profile. The DELETE
    # pre-check is tenant-scoped, so it counts zero and lets the DELETE through;
    # only the FK stops it. Without the IntegrityError handler this is a 500.
    other = client.post("/api/companies", headers=headers, json={"name": "Ağ Şirketi B"})
    assert other.status_code == 201, other.text
    other_id = int(other.json()["id"])
    with SessionLocal() as db:
        db.execute(text("""INSERT INTO supplier_price_imports(
            company_id,supplier_id,profile_id,status,source_filename,source_sha256,
            source_bytes,parsed_row_count,matched_count,unmatched_count,warning_count,
            blocked_count,created_at)
            VALUES(:cid,:sid,:pid,'DRY_RUN_READY','net.xlsx',:sha,1,0,0,0,0,0,:now)"""), {
            "cid": other_id, "sid": supplier_id, "pid": profile_id,
            "sha": "d" * 64, "now": datetime.now(timezone.utc),
        })
        db.commit()
        invisible = db.execute(text(
            """SELECT COUNT(*) FROM supplier_price_imports
               WHERE company_id=:cid AND profile_id=:id"""
        ), {"cid": company_a, "id": profile_id}).scalar_one()
    assert invisible == 0, "pre-check must not see the other company's import"

    response = client.delete(
        "/api/supplier-prices/profiles/" + str(profile_id), headers=headers
    )
    assert response.status_code == 409, (
        "expected 409 from the FK safety net, got "
        + str(response.status_code) + " " + response.text
    )
    assert response.json()["detail"] == IN_USE, response.text

    # The rollback left the profile intact and the session usable.
    assert client.get(
        "/api/supplier-prices/profiles/" + str(profile_id), headers=headers
    ).status_code == 200
    assert client.get(
        "/api/supplier-prices/profiles", headers=headers
    ).status_code == 200

print("SUPPLIER_PRICE_PROFILE_DELETE_NET_OK")
'''
