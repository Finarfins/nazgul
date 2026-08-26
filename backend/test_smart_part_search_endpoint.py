from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def run_endpoint_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from fastapi.testclient import TestClient

from app.main import app

with TestClient(app) as client:
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(body["companies"][0]["id"]),
    }
    changed = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={
            "current_password": "admin123",
            "new_password": "SmartSearch123!",
        },
    )
    assert changed.status_code == 200, changed.text
    headers["Authorization"] = "Bearer " + changed.json()["access_token"]

    def create_product(
        target_headers,
        name,
        identifier,
        *,
        identifier_field="oem_number",
        purchase_price=10,
        sale_price=20,
        location=None,
    ):
        product = {
            "name": name,
            "product_code": None,
            "oem_number": None,
            "barcode": None,
            "alternative_oem": None,
            "purchase_price": purchase_price,
            "sale_price": sale_price,
            "vat_rate": 20,
            "stock": 10,
            "unit": "Adet",
            "location": location,
        }
        product[identifier_field] = identifier
        response = client.post(
            "/api/products",
            headers=target_headers,
            json=product,
        )
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    actual_id = create_product(headers, "Gerçek Kodlu Parça", "8451-2763")
    ocr_id = create_product(headers, "OCR Yazımlı Parça", "845I-2763")

    exact = client.get(
        "/api/search/parts",
        headers=headers,
        params={"q": "8451-2763"},
    )
    assert exact.status_code == 200, exact.text
    exact_body = exact.json()
    assert exact_body["kind"] == "identifier"
    assert exact_body["items"][0]["id"] == actual_id
    assert exact_body["items"][0]["match_type"] == "oem_exact"

    tolerant = client.get(
        "/api/search/parts",
        headers=headers,
        params={"q": "845I-2763"},
    )
    assert tolerant.status_code == 200, tolerant.text
    tolerant_body = tolerant.json()
    assert tolerant_body["items"][0]["id"] == ocr_id
    assert tolerant_body["items"][0]["match_type"] == "oem_exact"
    assert actual_id in [item["id"] for item in tolerant_body["items"]]

    text_id = create_product(
        headers,
        "Motor Yağlama Pompası",
        "POMPA-01",
        identifier_field="product_code",
        purchase_price=17,
        sale_price=29,
        location="A-12",
    )
    text_search = client.get(
        "/api/search/parts",
        headers=headers,
        params={"q": "yağlama"},
    )
    assert text_search.status_code == 200, text_search.text
    text_item = next(
        item for item in text_search.json()["items"] if item["id"] == text_id
    )
    assert text_item["purchase_price"] == 17
    assert text_item["sale_price"] == 29
    assert text_item["location"] == "A-12"
    assert "warehouse_stock" in text_item

    short_code_id = create_product(
        headers,
        "Kısa Alfa Kod",
        "ABCD",
        identifier_field="product_code",
    )
    short_code = client.get(
        "/api/search/parts",
        headers=headers,
        params={"q": "ABCD"},
    )
    assert short_code.status_code == 200, short_code.text
    assert short_code.json()["items"][0]["id"] == short_code_id

    slash_id = create_product(
        headers,
        "Ters Bölü Kod",
        r"AB\123",
        identifier_field="product_code",
    )
    slash = client.get(
        "/api/search/parts",
        headers=headers,
        params={"q": "AB123"},
    )
    assert slash.status_code == 200, slash.text
    assert slash_id in [item["id"] for item in slash.json()["items"]]

    oem_ocr_id = create_product(
        headers,
        "OEM OCR Adayı",
        "ABC123",
        identifier_field="oem_number",
    )
    product_exact_id = create_product(
        headers,
        "Ürün Kodu Tam",
        "ABCI23",
        identifier_field="product_code",
    )
    barcode_exact_id = create_product(
        headers,
        "Barkod Tam",
        "ABCI23",
        identifier_field="barcode",
    )
    cross_field = client.get(
        "/api/search/parts",
        headers=headers,
        params={"q": "ABCI23"},
    )
    assert cross_field.status_code == 200, cross_field.text
    cross_ids = [item["id"] for item in cross_field.json()["items"]]
    assert cross_ids.index(product_exact_id) < cross_ids.index(oem_ocr_id)
    assert cross_ids.index(barcode_exact_id) < cross_ids.index(oem_ocr_id)

    oldest_exact_id = create_product(
        headers,
        "Eski Tam Eşleşme",
        "PQRI23",
        identifier_field="product_code",
    )
    for index in range(105):
        create_product(
            headers,
            f"Yeni OCR Adayı {index:03d}",
            "PQR123",
            identifier_field="oem_number",
        )
    boundary = client.get(
        "/api/search/parts",
        headers=headers,
        params={"q": "PQRI23", "limit": 5},
    )
    assert boundary.status_code == 200, boundary.text
    assert boundary.json()["items"][0]["id"] == oldest_exact_id

    low_price_id = create_product(
        headers,
        "Ortak Ürün Düşük",
        "SORT-LOW",
        identifier_field="product_code",
        sale_price=11,
    )
    high_price_id = create_product(
        headers,
        "Ortak Ürün Yüksek",
        "SORT-HIGH",
        identifier_field="product_code",
        sale_price=99,
    )
    sorted_text = client.get(
        "/api/search/parts",
        headers=headers,
        params={"q": "Ortak Ürün", "sort": "price_desc"},
    )
    assert sorted_text.status_code == 200, sorted_text.text
    sorted_ids = [item["id"] for item in sorted_text.json()["items"]]
    assert sorted_ids.index(high_price_id) < sorted_ids.index(low_price_id)

    truncated = client.get(
        "/api/search/parts",
        headers=headers,
        params={"q": "000000000000"},
    )
    assert truncated.status_code == 200, truncated.text
    assert truncated.json()["candidate_count"] == 24
    assert truncated.json()["candidates_truncated"] is True

    company = client.post(
        "/api/companies",
        headers=headers,
        json={"name": "Arama İzolasyon Firması"},
    )
    assert company.status_code == 201, company.text
    other_headers = {
        **headers,
        "X-Company-ID": str(company.json()["id"]),
    }
    other_id = create_product(other_headers, "Diğer Firma Parçası", "8451-2763")

    isolated = client.get(
        "/api/search/parts",
        headers=headers,
        params={"q": "8451-2763"},
    )
    assert isolated.status_code == 200, isolated.text
    isolated_ids = [item["id"] for item in isolated.json()["items"]]
    assert actual_id in isolated_ids
    assert other_id not in isolated_ids
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


def test_smart_part_search_endpoint_is_ocr_tolerant_and_tenant_scoped(
    tmp_path: Path,
) -> None:
    database = tmp_path / "smart-part-search.db"
    run_endpoint_smoke(f"sqlite:///{database.as_posix()}")
