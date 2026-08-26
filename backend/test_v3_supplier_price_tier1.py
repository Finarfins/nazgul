from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_tampar_slice_tier1_roundtrip(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'tier1.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", SCENARIO],
        env=env,
        capture_output=True,
        text=True,
        cwd=BACKEND,
        timeout=120,
    )
    assert "TAMPAR_TIER1_OK" in completed.stdout, completed.stdout + completed.stderr


SCENARIO = r'''
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app


def login(client, username, password):
    candidates = [password]
    if username == "admin":
        candidates.extend(["SupplierImport123!", "SupplierBridge123!"])
    for candidate in candidates:
        response = client.post(
            "/api/auth/login", json={"username": username, "password": candidate}
        )
        if response.status_code == 200:
            password = candidate
            break
    assert response.status_code == 200, response.text
    body = response.json()
    headers = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(body["companies"][0]["id"]),
    }
    if body["user"].get("must_change_password"):
        new_password = password + "Changed!"
        changed = client.post(
            "/api/auth/change-password",
            headers=headers,
            json={"current_password": password, "new_password": new_password},
        )
        assert changed.status_code == 200, changed.text
        headers["Authorization"] = "Bearer " + changed.json()["access_token"]
        password = new_password
    return headers, int(body["companies"][0]["id"]), password


def upload(client, headers, profile_id, content):
    return client.post(
        "/api/supplier-prices/imports",
        headers=headers,
        data={"profile_id": str(profile_id)},
        files={
            "file": (
                "tampar_slice.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )


with TestClient(app) as client:
    admin_headers, company_id, _ = login(client, "admin", "admin123")
    supplier = client.post(
        "/api/suppliers", headers=admin_headers, json={"name": "TAMPAR Tier 1"}
    )
    assert supplier.status_code == 201, supplier.text
    supplier_id = supplier.json()["id"]

    exact = client.post(
        "/api/products",
        headers=admin_headers,
        json={
            "name": "Exact OEM",
            "product_code": "INTERNAL-OEM",
            "oem_number": "17N-T222S",
            "purchase_price": "7.00",
            "sale_price": "19.00",
        },
    )
    assert exact.status_code == 201, exact.text
    exact_id = exact.json()["id"]
    xref_product = client.post(
        "/api/products",
        headers=admin_headers,
        json={
            "name": "Xref OEM",
            "product_code": "INTERNAL-XREF",
            "purchase_price": "8.00",
            "sale_price": "29.00",
        },
    )
    assert xref_product.status_code == 201, xref_product.text
    xref_id = xref_product.json()["id"]
    xref = client.post(
        "/api/supplier-prices/xrefs",
        headers=admin_headers,
        json={
            "supplier_id": supplier_id,
            "supplier_code": "17J-240EK",
            "part_id": xref_id,
        },
    )
    assert xref.status_code == 201, xref.text

    profile = client.post(
        "/api/supplier-prices/profiles",
        headers=admin_headers,
        json={
            "supplier_id": supplier_id,
            "name": "TAMPAR 07",
            "sheet_selector": "07_Zipka_Mastar",
            "header_row_strategy": "fixed:5",
            "column_map": {
                "code": "ÜRÜN No.",
                "description": "AÇIKLAMA",
                "price_cash": "PEŞİN FİYATI\nKDV DAHİL",
                "price_term": "60GÜN ÇEK veya\nKK 3 TAKSİT\nKDV DAHİL",
                "vat_rate": "KDV",
                "status_note": "N O T",
            },
            "currency_mode": "inline",
            "term_days": 60,
            "vat_source": "column",
            "warning_threshold": "25",
            "blocked_threshold": "1000",
        },
    )
    assert profile.status_code == 201, profile.text
    profile_id = profile.json()["id"]
    content = Path("tests/fixtures/tampar_slice.xlsx").read_bytes()
    created = upload(client, admin_headers, profile_id, content)
    assert created.status_code == 202, created.text
    import_id = created.json()["id"]

    report_response = client.get(
        f"/api/supplier-prices/imports/{import_id}", headers=admin_headers
    )
    assert report_response.status_code == 200, report_response.text
    report = report_response.json()
    assert report["matched_count"] == 2, report
    assert report["unmatched_count"] == 13, report
    assert report["review_count"] == 5, report
    assert report["change_summary"] == {
        "NEW_PRICE": 2,
        "CHANGED_PRICE": 0,
        "UNCHANGED": 0,
        "UNMATCHED": 13,
        "SKIPPED": 1,
        "REVIEW": 5,
    }
    assert report["tier_count"] == 17
    exact_line = next(line for line in report["lines"] if line["supplier_code"] == "17N-T222S")
    xref_line = next(line for line in report["lines"] if line["supplier_code"] == "17J-240EK")
    continued = next(
        line for line in report["lines"] if line["supplier_code"] == "17SCH-16500.04"
    )
    multi_price = next(
        line for line in report["lines"] if line["supplier_code"] == "17N-T841H"
    )
    no_price = next(
        line for line in report["lines"] if line["supplier_code"] == "17SCH-10721.01"
    )
    assert exact_line["match_source"] == "exact_oem" and exact_line["part_id"] == exact_id
    assert xref_line["match_source"] == "xref" and xref_line["part_id"] == xref_id
    assert exact_line["vat_included"] is True
    assert xref_line["status_note"].startswith("STOK AZ")
    assert xref_line["reason_code"] is None
    assert xref_line["reason_codes"]["status_note"] == "NO_PRICE_STATUS"
    assert continued["supplier_code"] == "17SCH-16500.04"
    assert multi_price["state"] == "REVIEW"
    assert multi_price["reason_code"] == "MULTIPLE_PRICES"
    assert multi_price["reason_codes"]["status_note"] == "MULTIPLE_PRICES"
    assert no_price["state"] == "REVIEW"
    assert no_price["reason_code"] == "NO_PRICE_STATUS"
    assert no_price["reason_codes"]["price_cash"] == "NO_PRICE_STATUS"
    assert no_price["reason_codes"]["price_term"] == "NO_PRICE_STATUS"

    with SessionLocal() as db:
        assert db.execute(
            text(
                "SELECT COUNT(*) FROM supplier_part_prices "
                "WHERE company_id=:cid AND supplier_id=:sid"
            ),
            {"cid": company_id, "sid": supplier_id},
        ).scalar_one() == 0
        stored = db.execute(
            text(
                "SELECT raw FROM supplier_price_import_lines "
                "WHERE company_id=:cid AND import_id=:iid AND supplier_code=:code"
            ),
            {"cid": company_id, "iid": import_id, "code": "17N-T841H"},
        ).scalar_one()
        assert "MULTIPLE_PRICES" in str(stored)

    user = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": "tier1_accounting",
            "display_name": "Tier 1 Accounting",
            "password": "Tier1Accounting123!",
            "role": "muhasebe",
        },
    )
    assert user.status_code in (200, 201), user.text
    accounting_headers, _, _ = login(
        client, "tier1_accounting", "Tier1Accounting123!"
    )
    applied = client.post(
        f"/api/supplier-prices/imports/{import_id}/apply",
        headers=accounting_headers,
        json={
            "expected_revision": report["report_digest"],
            "report_digest": report["report_digest"],
        },
    )
    assert applied.status_code == 200, applied.text

    with SessionLocal() as db:
        prices = db.execute(
            text(
                "SELECT part_id,price_cash,price_term,currency,vat_rate "
                "FROM supplier_part_prices "
                "WHERE company_id=:cid AND supplier_id=:sid ORDER BY part_id"
            ),
            {"cid": company_id, "sid": supplier_id},
        ).mappings().all()
        assert len(prices) == 2
        by_part = {row["part_id"]: row for row in prices}
        assert str(by_part[exact_id]["price_cash"]) in {"2.5300", "2.53"}
        assert str(by_part[exact_id]["price_term"]) in {"2.8600", "2.86"}
        assert by_part[exact_id]["currency"] == "EUR"
        assert str(by_part[exact_id]["vat_rate"]) in {"10.00", "10"}
        sale_prices = db.execute(
            text(
                "SELECT id,sale_price FROM products "
                "WHERE company_id=:cid AND id IN (:exact,:xref) ORDER BY id"
            ),
            {"cid": company_id, "exact": exact_id, "xref": xref_id},
        ).mappings().all()
        assert {row["id"]: Decimal(str(row["sale_price"])) for row in sale_prices} == {
            exact_id: Decimal("19.00"),
            xref_id: Decimal("29.00"),
        }

    duplicate = upload(client, admin_headers, profile_id, content)
    assert duplicate.status_code == 409, duplicate.text
    print(
        "TAMPAR_DISTRIBUTION "
        f"MATCHED={report['matched_count']} "
        f"UNMATCHED={report['unmatched_count']} "
        f"REVIEW={report['review_count']}"
    )
    print("TAMPAR_TIER1_OK")
'''
