"""Machine idempotency keys are bound to the request fingerprint.

Replaying an Idempotency-Key with the SAME body returns the original machine
(no duplicate); reusing the same key with a DIFFERENT body is a 409 instead of
silently returning the first machine. Exercises the new
machine_idempotency.request_fingerprint column (migration 20260722_0014).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_idempotency_key_is_bound_to_request_fingerprint(tmp_path: Path) -> None:
    database = tmp_path / "machine-idem-fp.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["COOKIE_SECURE"] = "false"

    smoke = r'''
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)

login = c.post("/api/auth/login", json={"username":"admin","password":"admin123"}).json()
cid = login["companies"][0]["id"]
h = {"Authorization":"Bearer "+login["access_token"], "X-Company-ID":str(cid)}
rot = c.post("/api/auth/change-password", headers=h, json={
    "current_password":"admin123","new_password":"MachineIdem!2026"}).json()
h = {"Authorization":"Bearer "+rot["access_token"], "X-Company-ID":str(cid)}

def post(body, key=None):
    hh = dict(h)
    if key is not None:
        hh["Idempotency-Key"] = key
    return c.post("/api/machines", headers=hh, json=body)

base = {"brand":"John Deere","model":"6155R"}

# Same key + same body -> replay the original machine (no duplicate).
r1 = post(base, key="K1"); assert r1.status_code == 201, r1.text
id1 = r1.json()["id"]
r2 = post(base, key="K1"); assert r2.status_code == 201, r2.text
assert r2.json()["id"] == id1, "same key+body must replay the same machine"

# Same key + DIFFERENT body -> 409 (key cannot be reused for a different request).
r3 = post({"brand":"John Deere","model":"9RX"}, key="K1")
assert r3.status_code == 409, f"key reuse with different body must 409: {r3.status_code} {r3.text}"

# A different key with a different body still creates a new machine.
r4 = post({"brand":"Case IH","model":"Magnum"}, key="K2")
assert r4.status_code == 201, r4.text
assert r4.json()["id"] != id1

# No duplicates were created: exactly two machines exist (K1 once, K2 once).
listing = c.get("/api/machines", headers={"X-Company-ID":str(cid), "Authorization":h["Authorization"]}).json()
assert len(listing) == 2, [m["model"] for m in listing]
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
