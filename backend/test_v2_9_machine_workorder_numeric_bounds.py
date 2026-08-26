"""Numeric upper bounds on MachineCreate / WorkOrderFields (SQLite side).

Oversized numeric input must be rejected at the schema edge (HTTP 422) so it
never reaches the NUMERIC columns. The PostgreSQL twin
(test_machine_workorder_bounds_postgresql.py) asserts the identical 422 on real
PG, proving parity (no NUMERIC overflow / 500 on PostgreSQL).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_numeric_upper_bounds_reject_oversized_input(tmp_path: Path) -> None:
    database = tmp_path / "numeric-bounds.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["COOKIE_SECURE"] = "false"

    smoke = r'''
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)

login = c.post("/api/auth/login", json={"username":"admin","password":"admin123"})
assert login.status_code == 200, login.text
company_id = login.json()["companies"][0]["id"]
tok = login.json()["access_token"]
h = {"Authorization":"Bearer "+tok, "X-Company-ID":str(company_id)}
rot = c.post("/api/auth/change-password", headers=h, json={
    "current_password":"admin123","new_password":"AdminRot!2026x"})
assert rot.status_code == 200, rot.text
h = {"Authorization":"Bearer "+rot.json()["access_token"], "X-Company-ID":str(company_id)}

def machine(**over):
    body = {"brand":"John Deere","model":"6155R"}
    body.update(over)
    return c.post("/api/machines", headers=h, json=body)

# machines.working_hours -> NUMERIC(12,2); max = 9999999999.99.
assert machine(working_hours="9999999999.99").status_code == 201, "column max must be accepted"
assert machine(working_hours="10000000000.00").status_code == 422, "over-max must be rejected"
assert machine(working_hours="9999999999.999").status_code == 422, "over-max (fractional) rejected"

def work_order(**over):
    body = {"machine_id":1, "technician_id":1}
    body.update(over)
    return c.post("/api/work-orders", headers=h, json=body)

# Oversized WorkOrder numerics -> 422 at validation (before any FK/DB touch).
# labor_rate -> NUMERIC(18,2) bound 1e8; hours -> NUMERIC(10,2) bound 1e6.
assert work_order(labor_rate="100000001").status_code == 422, "labor_rate over 1e8 rejected"
assert work_order(actual_hours="1000001").status_code == 422, "actual_hours over 1e6 rejected"
assert work_order(estimated_hours="1000001").status_code == 422, "estimated_hours over 1e6 rejected"
# A negative value is still rejected (lower bound unchanged).
assert work_order(labor_rate="-1").status_code == 422, "negative labor_rate rejected"
print("OK")
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "OK" in result.stdout
