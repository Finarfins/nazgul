from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_supplier_price_bridge_roundtrip_sqlite(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'bridge.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", SCENARIO],
        env=env,
        capture_output=True,
        text=True,
        cwd=BACKEND,
        timeout=120,
    )
    assert "SUPPLIER_PRICE_BRIDGE_OK" in completed.stdout, completed.stdout + completed.stderr
    print(next(
        line for line in completed.stdout.splitlines()
        if line.startswith("SUPPLIER_PRICE_DECIMAL_JSON=")
    ))


SCENARIO = r'''
from io import BytesIO
from threading import Barrier, Thread
from time import sleep

from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import text

from app.db import SessionLocal, engine
from app.main import app


def workbook_bytes(cash="100,00 TL", term="110,00 TL"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Tum_Tablolar"
    sheet.append(["Kod", "Açıklama", "Birim", "Peşin", "Vadeli"])
    sheet.append(["BRIDGE-001\nAlt açıklama", "Ana açıklama", "Adet", cash, term])
    sheet.append(["UNMATCHED-001", "Eşleşmemeli", "Adet", "50,00 TL", "55,00 TL"])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def login(client):
    password = "admin123"
    response = client.post("/api/auth/login", json={"username": "admin", "password": password})
    if response.status_code != 200:
        password = "SupplierImport123!"
        response = client.post("/api/auth/login", json={"username": "admin", "password": password})
    assert response.status_code == 200, response.text
    body = response.json()
    headers = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(body["companies"][0]["id"]),
    }
    if body["user"].get("must_change_password"):
        changed = client.post("/api/auth/change-password", headers=headers, json={
            "current_password": password, "new_password": "SupplierBridge123!",
        })
        assert changed.status_code == 200, changed.text
        headers["Authorization"] = "Bearer " + changed.json()["access_token"]
    return headers, int(body["companies"][0]["id"])


def upload(client, headers, profile_id, content, filename="tampar.xlsx"):
    return client.post(
        "/api/supplier-prices/imports",
        headers=headers,
        data={"profile_id": str(profile_id)},
        files={"file": (filename, content,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )


with TestClient(app) as client:
    headers, company_a = login(client)
    supplier = client.post("/api/suppliers", headers=headers, json={"name": "Bridge Supplier"})
    assert supplier.status_code == 201, supplier.text
    supplier_id = supplier.json()["id"]
    product = client.post("/api/products", headers=headers, json={
        "name": "Bridge Part", "product_code": "BRIDGE-001", "purchase_price": "1",
    })
    assert product.status_code == 201, product.text
    product_id = product.json()["id"]

    profile = client.post("/api/supplier-prices/profiles", headers=headers, json={
        "supplier_id": supplier_id,
        "name": "TAMPAR",
        "sheet_selector": "Tum_Tablolar",
        "header_row_strategy": "fixed:1",
        "column_map": {
            "code": "Kod", "description": "Açıklama", "unit": "Birim",
            "price_cash": "Peşin", "price_term": "Vadeli",
        },
        "currency_mode": "inline",
        "term_days": 60,
        "vat_source": "fixed:20",
    })
    assert profile.status_code == 201, profile.text
    profile_id = profile.json()["id"]
    content = workbook_bytes()
    wrong_extension = upload(client, headers, profile_id, content, "tampar.pdf")
    assert wrong_extension.status_code == 400, wrong_extension.text
    assert "Dosya türü profil ile uyuşmuyor" in wrong_extension.json()["detail"]
    wrong_magic = upload(client, headers, profile_id, b"%PDF-1.7", "tampar.xlsx")
    assert wrong_magic.status_code == 400, wrong_magic.text
    assert "Dosya türü profil ile uyuşmuyor" in wrong_magic.json()["detail"]
    created = upload(client, headers, profile_id, content)
    assert created.status_code == 202, created.text
    import_id = created.json()["id"]

    imports = client.get("/api/supplier-prices/imports", headers=headers)
    assert imports.status_code == 200, imports.text
    assert imports.json() == [{
        "id": import_id,
        "status": "DRY_RUN_READY",
        "source_filename": "tampar.xlsx",
        "parsed_row_count": 2,
        "created_at": imports.json()[0]["created_at"],
    }]

    report = client.get(f"/api/supplier-prices/imports/{import_id}", headers=headers)
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["status"] == "DRY_RUN_READY"
    assert body["matched_count"] == 1 and body["unmatched_count"] == 1
    matched = next(line for line in body["lines"] if line["supplier_code"] == "BRIDGE-001")
    unmatched = next(line for line in body["lines"] if line["supplier_code"] == "UNMATCHED-001")
    money_fields = (
        "old_price_cash", "old_price_term", "price_cash", "price_term", "deviation_pct"
    )
    print("INITIAL_SUPPLIER_PRICE_DECIMAL_JSON=" + str({
        field: matched[field] for field in money_fields
    }))
    assert {
        field: matched[field] for field in money_fields
    } == {
        "old_price_cash": None,
        "old_price_term": None,
        "price_cash": "100.0000",
        "price_term": "110.0000",
        "deviation_pct": "100.00",
    }
    assert all(
        value is None or isinstance(value, str)
        for line in body["lines"]
        for field in money_fields
        if (value := line[field]) is not None
    )
    assert matched["state"] == "BLOCKED" and matched["part_id"] == product_id
    assert matched["match_source"] == "exact_oem"
    assert matched["description"] == "Alt açıklama\nAna açıklama"
    assert unmatched["state"] == "UNMATCHED" and unmatched["part_id"] is None

    blocked = client.post(
        f"/api/supplier-prices/imports/{import_id}/apply",
        headers=headers,
        json={"expected_revision": body["report_digest"], "report_digest": body["report_digest"]},
    )
    assert blocked.status_code == 409, blocked.text
    limited_user = client.post("/api/users", headers=headers, json={
        "username": "bridge_accounting", "display_name": "Bridge Accounting",
        "password": "BridgeAccounting123!", "role": "muhasebe",
    })
    assert limited_user.status_code in (200, 201), limited_user.text
    limited_login = client.post("/api/auth/login", json={
        "username": "bridge_accounting", "password": "BridgeAccounting123!",
    })
    assert limited_login.status_code == 200, limited_login.text
    limited_headers = {
        "Authorization": "Bearer " + limited_login.json()["access_token"],
        "X-Company-ID": str(company_a),
    }
    limited_changed = client.post("/api/auth/change-password", headers=limited_headers, json={
        "current_password": "BridgeAccounting123!", "new_password": "BridgeAccounting456!",
    })
    assert limited_changed.status_code == 200, limited_changed.text
    limited_headers["Authorization"] = "Bearer " + limited_changed.json()["access_token"]
    assert client.post(
        f"/api/supplier-prices/imports/{import_id}/apply",
        headers=limited_headers,
        json={"expected_revision": body["report_digest"], "report_digest": body["report_digest"]},
    ).status_code == 409
    assert client.post(
        f"/api/supplier-prices/imports/{import_id}/lines/{matched['line_no']}/override",
        headers=limited_headers,
        json={"reason": "Yetkisiz kabul girişimi"},
    ).status_code == 403
    overridden = client.post(
        f"/api/supplier-prices/imports/{import_id}/lines/{matched['line_no']}/override",
        headers=headers,
        json={"reason": "Yeni tedarikçi başlangıç fiyatı doğrulandı"},
    )
    assert overridden.status_code == 200, overridden.text
    repeated_override = client.post(
        f"/api/supplier-prices/imports/{import_id}/lines/{matched['line_no']}/override",
        headers=headers,
        json={"reason": "Bu ikinci gerekçe audit kaydını ezmemeli"},
    )
    assert repeated_override.status_code == 200, repeated_override.text
    with SessionLocal() as db:
        override_row = db.execute(text("""SELECT override_reason,override_by,override_at
            FROM supplier_price_import_lines
            WHERE company_id=:cid AND import_id=:iid AND line_no=:line"""),
            {"cid": company_a, "iid": import_id, "line": matched["line_no"]}).mappings().one()
        assert override_row["override_reason"] == "Yeni tedarikçi başlangıç fiyatı doğrulandı"
        assert override_row["override_by"] is not None and override_row["override_at"] is not None
        assert db.execute(text("""SELECT COUNT(*) FROM activity_logs
            WHERE company_id=:cid AND action_type='supplier_price.block_overridden'
            AND resource_id=:iid"""),
            {"cid": company_a, "iid": import_id}).scalar_one() == 1

    applied = client.post(
        f"/api/supplier-prices/imports/{import_id}/apply",
        headers=headers,
        json={"expected_revision": body["report_digest"], "report_digest": body["report_digest"]},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["revision"] == 1
    with SessionLocal() as db:
        price = db.execute(text("""SELECT price_cash,price_term,currency,term_days
            FROM supplier_part_prices
            WHERE company_id=:cid AND supplier_id=:sid AND part_id=:pid"""),
            {"cid": company_a, "sid": supplier_id, "pid": product_id}).mappings().one()
        assert str(price["price_cash"]) in {"100.0000", "100"}
        assert str(price["price_term"]) in {"110.0000", "110"}
        assert price["currency"] == "TRY" and price["term_days"] == 60
        assert db.execute(text("""SELECT COUNT(*) FROM supplier_part_prices
            WHERE company_id=:cid AND supplier_id=:sid"""),
            {"cid": company_a, "sid": supplier_id}).scalar_one() == 1
        assert db.execute(text("""SELECT COUNT(*) FROM activity_logs
            WHERE company_id=:cid AND action_type='supplier_price.block_overridden'"""),
            {"cid": company_a}).scalar_one() == 1

    company_b = client.post("/api/companies", headers=headers, json={"name": "Bridge Company B"})
    assert company_b.status_code == 201, company_b.text
    foreign_headers = {**headers, "X-Company-ID": str(company_b.json()["id"])}
    assert client.get("/api/supplier-prices/imports", headers=foreign_headers).json() == []
    assert client.get(f"/api/supplier-prices/imports/{import_id}", headers=foreign_headers).status_code == 404
    foreign_apply = client.post(
        f"/api/supplier-prices/imports/{import_id}/apply",
        headers=foreign_headers,
        json={"expected_revision": body["report_digest"], "report_digest": body["report_digest"]},
    )
    assert foreign_apply.status_code == 404

    duplicate = upload(client, headers, profile_id, content)
    assert duplicate.status_code in (202, 409), duplicate.text
    if duplicate.status_code == 202:
        duplicate_report = client.get(
            f"/api/supplier-prices/imports/{duplicate.json()['id']}", headers=headers
        ).json()
        duplicate_apply = client.post(
            f"/api/supplier-prices/imports/{duplicate.json()['id']}/apply",
            headers=headers,
            json={
                "expected_revision": duplicate_report["report_digest"],
                "report_digest": duplicate_report["report_digest"],
            },
        )
        assert duplicate_apply.status_code == 409, duplicate_apply.text

    with SessionLocal() as db:
        db.execute(text("""UPDATE supplier_part_prices SET price_cash=101
            WHERE company_id=:cid AND supplier_id=:sid AND part_id=:pid"""),
            {"cid": company_a, "sid": supplier_id, "pid": product_id})
        db.commit()
    changed_revert = client.post(f"/api/supplier-prices/imports/{import_id}/revert", headers=headers)
    assert changed_revert.status_code == 409, changed_revert.text
    with SessionLocal() as db:
        db.execute(text("""UPDATE supplier_part_prices SET price_cash=100
            WHERE company_id=:cid AND supplier_id=:sid AND part_id=:pid"""),
            {"cid": company_a, "sid": supplier_id, "pid": product_id})
        db.commit()

    second = upload(client, headers, profile_id, workbook_bytes("120,00 TL", "130,00 TL"))
    assert second.status_code == 202, second.text
    second_id = second.json()["id"]
    second_report = client.get(f"/api/supplier-prices/imports/{second_id}", headers=headers).json()
    second_matched = next(
        line for line in second_report["lines"] if line["supplier_code"] == "BRIDGE-001"
    )
    decimal_contract = {field: second_matched[field] for field in money_fields}
    assert decimal_contract == {
        "old_price_cash": "100.0000",
        "old_price_term": "110.0000",
        "price_cash": "120.0000",
        "price_term": "130.0000",
        "deviation_pct": "20.00",
    }
    assert all(isinstance(value, str) for value in decimal_contract.values())
    print("SUPPLIER_PRICE_DECIMAL_JSON=" + str(decimal_contract))
    second_apply = client.post(
        f"/api/supplier-prices/imports/{second_id}/apply",
        headers=headers,
        json={
            "expected_revision": second_report["report_digest"],
            "report_digest": second_report["report_digest"],
        },
    )
    assert second_apply.status_code == 200, second_apply.text
    assert second_apply.json()["revision"] == 2
    old_revert = client.post(f"/api/supplier-prices/imports/{import_id}/revert", headers=headers)
    assert old_revert.status_code == 409, old_revert.text
    reverted = client.post(f"/api/supplier-prices/imports/{second_id}/revert", headers=headers)
    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["revision"] == 3
    with SessionLocal() as db:
        restored = db.execute(text("""SELECT price_cash,price_term FROM supplier_part_prices
            WHERE company_id=:cid AND supplier_id=:sid AND part_id=:pid"""),
            {"cid": company_a, "sid": supplier_id, "pid": product_id}).mappings().one()
        assert str(restored["price_cash"]) in {"100.0000", "100"}
        assert str(restored["price_term"]) in {"110.0000", "110"}

    if engine.dialect.name == "postgresql":
        raced = upload(client, headers, profile_id, workbook_bytes("140,00 TL", "150,00 TL"))
        assert raced.status_code == 202, raced.text
        raced_id = raced.json()["id"]
        raced_report = client.get(f"/api/supplier-prices/imports/{raced_id}", headers=headers).json()
        payload = {
            "expected_revision": raced_report["report_digest"],
            "report_digest": raced_report["report_digest"],
        }
        gate = Barrier(3)
        results = []

        def concurrent_apply():
            gate.wait()
            response = client.post(
                f"/api/supplier-prices/imports/{raced_id}/apply", headers=headers, json=payload
            )
            results.append(response.status_code)

        with SessionLocal() as blocker:
            blocker.execute(text("""SELECT id FROM supplier_part_prices
                WHERE company_id=:cid AND supplier_id=:sid AND part_id=:pid FOR UPDATE"""),
                {"cid": company_a, "sid": supplier_id, "pid": product_id}).first()
            workers = [Thread(target=concurrent_apply) for _ in range(2)]
            for worker in workers:
                worker.start()
            gate.wait()
            sleep(0.3)
            blocker.rollback()
            for worker in workers:
                worker.join(10)
        assert sorted(results) == [200, 409], results
        with SessionLocal() as db:
            assert db.execute(text("""SELECT COUNT(*) FROM supplier_part_prices
                WHERE company_id=:cid AND supplier_id=:sid AND part_id=:pid"""),
                {"cid": company_a, "sid": supplier_id, "pid": product_id}).scalar_one() == 1
            assert db.execute(text("""SELECT revision FROM supplier_price_imports
                WHERE company_id=:cid AND id=:id"""),
                {"cid": company_a, "id": raced_id}).scalar_one() == 4

        interleaved = upload(client, headers, profile_id, workbook_bytes("160,00 TL", "170,00 TL"))
        assert interleaved.status_code == 202, interleaved.text
        interleaved_id = interleaved.json()["id"]
        interleaved_report = client.get(
            f"/api/supplier-prices/imports/{interleaved_id}", headers=headers
        ).json()
        interleaved_payload = {
            "expected_revision": interleaved_report["report_digest"],
            "report_digest": interleaved_report["report_digest"],
        }
        apply_result = []
        with SessionLocal() as blocker:
            blocker.execute(text("""SELECT id FROM supplier_part_prices
                WHERE company_id=:cid AND supplier_id=:sid AND part_id=:pid FOR UPDATE"""),
                {"cid": company_a, "sid": supplier_id, "pid": product_id}).first()
            worker = Thread(target=lambda: apply_result.append(client.post(
                f"/api/supplier-prices/imports/{interleaved_id}/apply",
                headers=headers, json=interleaved_payload,
            ).status_code))
            worker.start()
            sleep(0.3)
            losing_revert = client.post(
                f"/api/supplier-prices/imports/{interleaved_id}/revert", headers=headers
            )
            assert losing_revert.status_code == 409, losing_revert.text
            blocker.rollback()
            worker.join(10)
        assert apply_result == [200], apply_result

print("SUPPLIER_PRICE_BRIDGE_OK")
'''
