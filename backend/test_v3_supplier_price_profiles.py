from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parent


def test_supplier_price_profiles_sqlite(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'profiles.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", SCENARIO],
        env=env,
        capture_output=True,
        text=True,
        cwd=BACKEND,
        timeout=120,
    )
    assert "SUPPLIER_PRICE_PROFILES_OK" in completed.stdout, (
        completed.stdout + completed.stderr
    )


SCENARIO = r'''
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app


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
        new_password = "SupplierProfiles456!"
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


with TestClient(app) as client:
    headers, company_a = login(client)
    supplier = client.post("/api/suppliers", headers=headers, json={"name": "Profil Tedarikçisi"})
    assert supplier.status_code == 201, supplier.text
    supplier_id = supplier.json()["id"]

    first = client.post(
        "/api/supplier-prices/profiles", headers=headers,
        json=payload(supplier_id, "Alfa XLSX"),
    )
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]
    second = client.post(
        "/api/supplier-prices/profiles", headers=headers,
        json=payload(supplier_id, "Beta XLSX"),
    )
    assert second.status_code == 201, second.text
    second_id = second.json()["id"]

    profiles = client.get("/api/supplier-prices/profiles", headers=headers)
    assert profiles.status_code == 200, profiles.text
    assert [item["name"] for item in profiles.json()] == ["Alfa XLSX", "Beta XLSX"]
    summary = profiles.json()[0]
    assert summary["supplier_name"] == "Profil Tedarikçisi"
    assert summary["warning_threshold"] == "25.00"
    assert summary["blocked_threshold"] == "100.00"
    assert summary["import_count"] == 0
    assert "column_map" not in summary and "page_sections" not in summary

    detail = client.get(
        f"/api/supplier-prices/profiles/{first_id}", headers=headers
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["column_map"]["price_cash"] == "Peşin"
    assert detail.json()["page_sections"] == []

    missing_mapping = payload(supplier_id, "Eksik")
    missing_mapping["column_map"].pop("price_term")
    response = client.put(
        f"/api/supplier-prices/profiles/{first_id}",
        headers=headers, json=missing_mapping,
    )
    assert response.status_code == 422, response.text
    assert "Kod, peşin ve vadeli fiyat kolonları zorunludur" in response.text

    bad_threshold = payload(supplier_id, "Eşik")
    bad_threshold["warning_threshold"] = "101"
    bad_threshold["blocked_threshold"] = "100"
    response = client.put(
        f"/api/supplier-prices/profiles/{first_id}",
        headers=headers, json=bad_threshold,
    )
    assert response.status_code == 400, response.text
    assert "Blok eşiği uyarı eşiğinden küçük olamaz" in response.text

    overlapping_pdf = payload(supplier_id, "PDF")
    overlapping_pdf.update({
        "source_type": "pdf",
        "column_map": {},
        "page_sections": [
            {"page_start": 1, "page_end": 3, "column_map": {
                "code": 0, "price_cash": 2, "price_term": 3,
            }},
            {"page_start": 3, "page_end": 4, "column_map": {
                "code": 0, "price_cash": 2, "price_term": 3,
            }},
        ],
    })
    response = client.put(
        f"/api/supplier-prices/profiles/{first_id}",
        headers=headers, json=overlapping_pdf,
    )
    assert response.status_code == 422, response.text
    assert "PDF bölümleri 1 ve 2 çakışıyor" in response.text

    duplicate = payload(supplier_id, "Alfa XLSX")
    response = client.put(
        f"/api/supplier-prices/profiles/{second_id}",
        headers=headers, json=duplicate,
    )
    assert response.status_code == 409, response.text
    assert "aynı adlı bir eşleme profili zaten var" in response.json()["detail"]

    company_b = client.post("/api/companies", headers=headers, json={"name": "Profil Şirketi B"})
    assert company_b.status_code == 201, company_b.text
    foreign_headers = {**headers, "X-Company-ID": str(company_b.json()["id"])}
    assert client.get("/api/supplier-prices/profiles", headers=foreign_headers).json() == []
    assert client.get(
        f"/api/supplier-prices/profiles/{first_id}", headers=foreign_headers
    ).status_code == 404
    assert client.put(
        f"/api/supplier-prices/profiles/{first_id}",
        headers=foreign_headers, json=payload(supplier_id, "Yabancı"),
    ).status_code == 404
    assert client.delete(
        f"/api/supplier-prices/profiles/{first_id}", headers=foreign_headers
    ).status_code == 404

    limited = client.post("/api/users", headers=headers, json={
        "username": "profile_depo", "display_name": "Profil Depo",
        "password": "ProfileDepo123!", "role": "depo",
    })
    assert limited.status_code in (200, 201), limited.text
    limited_headers, _ = login(client, "profile_depo", "ProfileDepo123!")
    limited_headers["X-Company-ID"] = str(company_a)
    assert client.get(
        "/api/supplier-prices/profiles", headers=limited_headers
    ).status_code == 200
    assert client.put(
        f"/api/supplier-prices/profiles/{first_id}",
        headers=limited_headers, json=payload(supplier_id, "Yetkisiz"),
    ).status_code == 403
    assert client.delete(
        f"/api/supplier-prices/profiles/{first_id}", headers=limited_headers
    ).status_code == 403

    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.execute(text("""INSERT INTO supplier_price_imports(
            company_id,supplier_id,profile_id,status,source_filename,source_sha256,
            source_bytes,parsed_row_count,matched_count,unmatched_count,warning_count,
            blocked_count,created_at)
            VALUES(:cid,:sid,:pid,'DRY_RUN_READY','used.xlsx',:sha,1,0,0,0,0,0,:now)"""), {
            "cid": company_a, "sid": supplier_id, "pid": first_id,
            "sha": "a" * 64, "now": now,
        })
        db.commit()
    used = client.delete(
        f"/api/supplier-prices/profiles/{first_id}", headers=headers
    )
    assert used.status_code == 409, used.text
    assert used.json()["detail"] == "Bu profille yapılmış içe aktarımlar var; profil silinemez."
    refreshed = client.get("/api/supplier-prices/profiles", headers=headers).json()
    assert next(item for item in refreshed if item["id"] == first_id)["import_count"] == 1

    removed = client.delete(
        f"/api/supplier-prices/profiles/{second_id}", headers=headers
    )
    assert removed.status_code == 200 and removed.json() == {"ok": True}
    assert client.get(
        f"/api/supplier-prices/profiles/{second_id}", headers=headers
    ).status_code == 404

print("SUPPLIER_PRICE_PROFILES_OK")
'''
