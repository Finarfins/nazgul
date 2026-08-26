"""Tenant scoping of change_history restore (exists-guard company_id).

restore_deleted resolves the delete log by company_id and its re-insert
exists-guard is now scoped `WHERE id=:id AND company_id=:cid`. This proves a
company cannot restore another company's deletion, and that a legitimate
same-company restore still succeeds (exercising the scoped exists-guard).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_restore_is_tenant_scoped(tmp_path: Path) -> None:
    dbfile = tmp_path / "history-tenant.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{dbfile.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["COOKIE_SECURE"] = "false"

    smoke = r'''
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)

r = c.post("/api/auth/login", json={"username":"admin","password":"admin123"}); assert r.status_code==200, r.text
company_a = r.json()["companies"][0]["id"]
csrf = c.cookies.get("yhp_csrf_token"); ha = {"X-Company-ID":str(company_a),"X-CSRF-Token":csrf}
r = c.post("/api/auth/change-password", headers=ha, json={"current_password":"admin123","new_password":"AdminRot!2026x"}); assert r.status_code==200, r.text
csrf = c.cookies.get("yhp_csrf_token"); ha = {"X-Company-ID":str(company_a),"X-CSRF-Token":csrf}

# Second tenant.
company_b = c.post("/api/companies", headers=ha, json={"name":"Tenant B"}).json()["id"]
hb = {"X-Company-ID":str(company_b),"X-CSRF-Token":csrf}

def mk(headers, name):
    return c.post("/api/customers", headers=headers, json={
        "name":name,"phone":"111","opening_balance":"0","risk_limit":"0",
        "payment_term_days":0,"is_active":True})

# Create both tenants' customers first so their global ids stay distinct.
# (B's live row keeps the max id, so deleting A's row leaves a free slot A can
# restore into. PostgreSQL never recycles ids; this ordering keeps the SQLite
# gate faithful to that.)
a_id = mk(ha, "Cari A").json()["id"]
b_id = mk(hb, "Cari B").json()["id"]
assert b_id != a_id

# Company A deletes its customer -> a restorable delete log.
assert c.delete(f"/api/customers/{a_id}", headers=ha).status_code == 204
a_logs = c.get(f"/api/history/customer/{a_id}", headers={"X-Company-ID":str(company_a)}).json()
a_delete_log = next(x for x in a_logs if x["action"] == "delete")["id"]

# ISOLATION: company B cannot restore company A's delete log.
resp = c.post(f"/api/history/restore/{a_delete_log}", headers=hb)
assert resp.status_code == 404, f"B must not restore A's log: {resp.status_code} {resp.text}"
# A's history log is not even visible to B.
assert c.get(f"/api/history/customer/{a_id}", headers={"X-Company-ID":str(company_b)}).json() == []

# LEGITIMATE: company A restores its own deletion (exercises the scoped
# exists-guard -> no A-owned row present -> re-insert succeeds).
ok = c.post(f"/api/history/restore/{a_delete_log}", headers=ha)
assert ok.status_code == 200, ok.text
assert ok.json()["name"] == "Cari A"

# Restored row is owned by A only.
assert c.get(f"/api/customers/{a_id}", headers={"X-Company-ID":str(company_a)}).status_code == 200
assert c.get(f"/api/customers/{a_id}", headers={"X-Company-ID":str(company_b)}).status_code == 404
print("OK")
'''
    r = subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stdout + "\n" + r.stderr
    assert "OK" in r.stdout
