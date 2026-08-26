"""GET /api/machines liste sorgu sözleşmesi (SQLite tarafı).

Canlıda İş Emri diyaloğu `limit=1000` gönderiyor, uç ise `Query(..., le=500)`
ile sınırlı olduğu için 422 dönüyordu; makine kutusu bu yüzden hiç dolmuyordu.
Bu test sınırın kendisini sabitler: 500 kabul edilir, üstü 422'dir. Sınır bir
gün gevşetilirse burası kırılır ve frontend limiti (MACHINE_OPTIONS_LIMIT)
bilinçli olarak birlikte güncellenir.

`active=all` de burada doğrulanır — diyalog pasif makineleri de listelediği
için bu değerin kabul edilmeye devam etmesi gerekir.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_machines_list_query_contract(tmp_path: Path) -> None:
    database = tmp_path / "machines-list-contract.db"
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
h = {"Authorization":"Bearer "+login.json()["access_token"], "X-Company-ID":str(company_id)}
rot = c.post("/api/auth/change-password", headers=h, json={
    "current_password":"admin123","new_password":"AdminRot!2026x"})
assert rot.status_code == 200, rot.text
h = {"Authorization":"Bearer "+rot.json()["access_token"], "X-Company-ID":str(company_id)}

def machines(**params):
    return c.get("/api/machines", headers=h, params=params)

# İş emri diyaloğunun gönderdiği sorgu kabul edilmeli (regresyon kilidi).
ok = machines(active="all", limit=500)
assert ok.status_code == 200, ok.text

# limit üst sınırı: 500 kabul, 501 ve üstü 422.
assert machines(active="all", limit=501).status_code == 422, "limit>500 reddedilmeli"
detail = machines(active="all", limit=1000)
assert detail.status_code == 422, "canlıdaki limit=1000 hâlâ 422 olmalı"
assert detail.json()["detail"][0]["loc"] == ["query","limit"], detail.text

# Alt sınır ve offset sınırı da sözleşmenin parçası.
assert machines(limit=0).status_code == 422, "limit<1 reddedilmeli"
assert machines(limit=500, offset=100_001).status_code == 422, "offset üst sınırı"

# active=all / inactive / active hepsi geçerli; limit varsayılanı da çalışır.
for active in ("all", "active", "inactive"):
    r = machines(active=active)
    assert r.status_code == 200, (active, r.text)
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
