from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_supplier_price_pdf_fail_closed_sqlite(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'pdf.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", SCENARIO],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "SUPPLIER_PRICE_PDF_OK" in completed.stdout, (
        completed.stdout + completed.stderr
    )


SCENARIO = r'''
from io import BytesIO

from fastapi.testclient import TestClient
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.main import app


def pdf_bytes():
    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4)
    normal = Table([
        ["Kod", "Açıklama", "Peşin", "Vadeli"],
        ["PDF-OK", "Normal", "10,00 TL", "11,00 TL"],
        ["PDF-MISSING", "Fiyat yok", "", ""],
    ])
    suspect = Table([["PDF-SUSPECT", "Eksik", "999,00 TL"]])
    for table in (normal, suspect):
        table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))
    document.build([
        normal,
        Spacer(1, 12),
        suspect,
        PageBreak(),
        Paragraph("Bu sayfada tablo yok."),
        PageBreak(),
        Paragraph("Bu sayfa profil kapsamı dışında."),
    ])
    return output.getvalue()


with TestClient(app) as client:
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(body["companies"][0]["id"]),
    }
    changed = client.post("/api/auth/change-password", headers=headers, json={
        "current_password": "admin123", "new_password": "SupplierPdf123!",
    })
    assert changed.status_code == 200, changed.text
    headers["Authorization"] = "Bearer " + changed.json()["access_token"]
    supplier = client.post(
        "/api/suppliers", headers=headers, json={"name": "PDF Supplier"}
    )
    assert supplier.status_code == 201, supplier.text
    profile = client.post("/api/supplier-prices/profiles", headers=headers, json={
        "supplier_id": supplier.json()["id"],
        "name": "PDF",
        "source_type": "pdf",
        "page_sections": [{
            "page_start": 1,
            "page_end": 2,
            "column_map": {
                "code": 0, "description": 1, "price_cash": 2, "price_term": 3,
            },
        }],
        "currency_mode": "inline",
        "term_days": 60,
        "vat_source": "fixed:20",
    })
    assert profile.status_code == 201, profile.text
    overlap = client.post("/api/supplier-prices/profiles", headers=headers, json={
        "supplier_id": supplier.json()["id"],
        "name": "PDF overlap",
        "source_type": "pdf",
        "page_sections": [
            {
                "page_start": 1, "page_end": 3,
                "column_map": {
                    "code": 0, "price_cash": 2, "price_term": 3,
                },
            },
            {
                "page_start": 3, "page_end": 4,
                "column_map": {
                    "code": 0, "price_cash": 2, "price_term": 3,
                },
            },
        ],
        "currency_mode": "inline",
        "term_days": 60,
        "vat_source": "fixed:20",
    })
    assert overlap.status_code == 422, overlap.text
    assert "çakışıyor" in overlap.text

    def assert_runtime_overlap(name, sections):
        runtime_profile = client.post(
            "/api/supplier-prices/profiles",
            headers=headers,
            json={
                "supplier_id": supplier.json()["id"],
                "name": name,
                "source_type": "pdf",
                "page_sections": sections,
                "currency_mode": "inline",
                "term_days": 60,
                "vat_source": "fixed:20",
            },
        )
        assert runtime_profile.status_code == 201, runtime_profile.text
        response = client.post(
            "/api/supplier-prices/imports",
            headers=headers,
            data={"profile_id": str(runtime_profile.json()["id"])},
            files={
                "file": (
                    f"{name}.pdf",
                    pdf_bytes(),
                    "application/pdf",
                )
            },
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == (
            "sayfa 1: 'bölüm 1' ve 'bölüm 2' "
            "çakışıyor — profili netleştirin"
        )

    mapping = {
        "code": 0, "description": 1, "price_cash": 2, "price_term": 3,
    }
    assert_runtime_overlap("range-signature", [
        {"page_start": 1, "page_end": 1, "column_map": mapping},
        {"header_signature": "PDF-OK", "column_map": mapping},
    ])
    assert_runtime_overlap("two-signatures", [
        {"header_signature": "PDF-OK", "column_map": mapping},
        {"header_signature": "PDF-MISSING", "column_map": mapping},
    ])

    created = client.post(
        "/api/supplier-prices/imports",
        headers=headers,
        data={"profile_id": str(profile.json()["id"])},
        files={"file": ("prices.pdf", pdf_bytes(), "application/pdf")},
    )
    assert created.status_code == 202, created.text
    report = client.get(
        f"/api/supplier-prices/imports/{created.json()['id']}", headers=headers
    )
    assert report.status_code == 200, report.text
    suspect = next(
        line for line in report.json()["lines"]
        if line["supplier_code"] == "PDF-SUSPECT"
    )
    assert suspect["state"] == "REVIEW"
    assert suspect["reason_code"] == "EXTRACTION_SUSPECT"
    assert suspect["price_cash"] is None and suspect["price_term"] is None
    missing = next(
        line for line in report.json()["lines"]
        if line["supplier_code"] == "PDF-MISSING"
    )
    assert missing["state"] == "REVIEW"
    assert missing["reason_code"] == "MISSING_PRICE"
    body = report.json()
    assert body["state_total"] == body["parsed_row_count"]
    assert body["state_total"] == sum(body["state_summary"].values())
    assert body["extraction_report"] == {
        "total_page_count": 3,
        "matched_page_count": 2,
        "processed_page_count": 1,
        "tableless_pages": [2],
        "uncovered_page_count": 1,
        "uncovered_pages": [3],
        "warnings": ["sayfa 2: tablo çıkarılamadı"],
    }

print("SUPPLIER_PRICE_PDF_OK")
'''
