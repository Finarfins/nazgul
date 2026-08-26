"""Aynı ürünün aynı belgedeki mükerrer satırları birleştirilmeli.

BizimHesap satış raporu satır bazlıdır: aynı ürün, aynı müşteri-gün belgesinde
birden fazla satırda görünebilir. Belge modeli bir üründen tek satır kabul
ettiği için (``TransactionCreate`` doğrulaması) içe aktarma bu satırları
birleştirmezse tüm dosya 400 ile reddedilirdi — gerçek bir müşteri dosyasında
1925 satırın 38'i bu durumdaydı.

Birleştirmenin para sözleşmesi: miktarlar ve KDV dahil tutarlar toplanır, birim
fiyat en sonda ``toplam tutar / toplam miktar`` olarak türetilir. Satır başına
yuvarlayıp toplamak yerine önce toplayıp sonra bölmek, belge toplamını kaynak
rapordaki toplamla kuruşuna kadar eşit tutar.

Uçtan uca akış izole bir SQLite veritabanına karşı alt süreçte koşar
(``DATABASE_URL`` motor import'unda bağlanır).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_duplicate_product_rows_are_merged_without_losing_a_kurus(tmp_path: Path) -> None:
    database = tmp_path / "sales_import_duplicates.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from decimal import Decimal
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.main import app


def sales_workbook(rows):
    """BizimHesap düzeni: başlık satırı başlık/tarih bloğunun ALTINDA (3. satır)."""
    wb = Workbook()
    ws = wb.active
    ws.append(["SATIŞ RAPORU"])
    ws.append(["01.01.2026 - 31.12.2026"])
    ws.append(["Müşteri", "Ürün", "Kod", "Miktar", "Net", "TOPLAM", "KDV(%)", "Tarih"])
    for row in rows:
        ws.append(row)
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


with TestClient(app) as client:
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(body["companies"][0]["id"]),
    }
    changed = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": "admin123", "new_password": "MergeLines123!"},
    )
    assert changed.status_code == 200, changed.text
    headers["Authorization"] = "Bearer " + changed.json()["access_token"]

    customer = client.post("/api/customers", headers=headers, json={"name": "Mükerrer Satır Müşterisi"})
    assert customer.status_code == 201, customer.text
    product = client.post(
        "/api/products",
        headers=headers,
        json={
            "name": "Mükerrer Ürün",
            "product_code": "MUK-001",
            "purchase_price": "50",
            "sale_price": "60",
            "vat_rate": 20,
            "stock": "100.0000",
            "unit": "Adet",
        },
    )
    assert product.status_code == 201, product.text
    stock_before = client.get("/api/products?q=MUK-001", headers=headers).json()[0]["stock"]

    # Aynı müşteri + aynı gün + AYNI ürün, iki ayrı satırda.
    content = sales_workbook([
        ["Mükerrer Satır Müşterisi", "Mükerrer Ürün", "MUK-001", 2, 100.00, 120.00, 20, "05.03.2026"],
        ["Mükerrer Satır Müşterisi", "Mükerrer Ürün", "MUK-001", 3, 150.00, 180.00, 20, "05.03.2026"],
    ])
    response = client.post(
        "/api/imports/sales/excel",
        headers=headers,
        files={
            "file": (
                "satis.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    # Birleştirme olmadan bu istek 400 ile reddedilirdi.
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["documents_created"] == 1, result
    assert result["lines_imported"] == 1, result          # iki satır tek kaleme indi
    assert result["merged_duplicate_rows"] == 1, result
    assert result["unmatched_customer_count"] == 0, result
    assert result["unmatched_product_count"] == 0, result

    listed = client.get("/api/orders?q=Mükerrer Satır Müşterisi", headers=headers).json()
    assert len(listed) == 1, listed
    document = listed[0]
    # Kuruşuna eşit: 120.00 + 180.00 = 300.00 (KDV dahil satır toplamları).
    assert Decimal(str(document["final_total"])) == Decimal("300.00"), document

    detail = client.get(f"/api/orders/{document['id']}", headers=headers).json()
    items = detail["items"]
    assert len(items) == 1, items
    assert Decimal(str(items[0]["quantity"])) == Decimal("5"), items      # 2 + 3
    assert Decimal(str(items[0]["unit_price"])) == Decimal("60.00"), items  # 300 / 5

    # Taslak kaldığı için stok değişmemeli.
    stock_after = client.get("/api/products?q=MUK-001", headers=headers).json()[0]["stock"]
    assert Decimal(str(stock_after)) == Decimal(str(stock_before)), (stock_before, stock_after)
    assert document["status"] == "draft", document

    print("SALES_IMPORT_MERGE_OK")
'''
    completed = subprocess.run(
        [sys.executable, "-c", smoke], env=env, capture_output=True, text=True, cwd=str(BACKEND)
    )
    assert "SALES_IMPORT_MERGE_OK" in completed.stdout, completed.stdout + completed.stderr
