"""Cari ekstre: devir, yürüyen bakiye, tenant izolasyonu ve PDF çıktısı.

Senaryo izole bir SQLite üzerinde ayrı bir süreçte koşar (bkz.
``test_cari_360_bounded_summary.py``): ``app.config.Settings`` modül düzeyinde
tek örnektir, bu yüzden veritabanı seçimi süreç başlamadan yapılmalıdır.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


BACKEND = Path(__file__).resolve().parent


def test_statement_opening_running_balance_tenancy_and_pdf(tmp_path: Path) -> None:
    database = tmp_path / "cari-ekstre.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)

    scenario = r'''
import os
import sqlite3

from fastapi.testclient import TestClient
from app.main import app


def ok(response):
    assert response.status_code < 300, (response.status_code, response.text)
    return response.json() if response.content else None


client = TestClient(app)
login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
assert login.status_code == 200, login.text
payload = login.json()
company_id = int(payload["companies"][0]["id"])
headers = {
    "Authorization": "Bearer " + payload["access_token"],
    "X-Company-ID": str(company_id),
}
if payload["user"].get("must_change_password"):
    changed = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": "admin123", "new_password": "Ekstre-Guvenli!2026"},
    )
    assert changed.status_code == 200, changed.text
    headers["Authorization"] = "Bearer " + changed.json()["access_token"]

customer = ok(client.post("/api/customers", headers=headers, json={
    "name": "Ekstre Çiftçi", "opening_balance": 100, "risk_limit": 0,
    "payment_term_days": 30, "is_active": True, "tax_number": "1234567890",
    "address": "Ovaköy Mah. Şığşığ Sok. No:3",
}))
supplier = ok(client.post("/api/suppliers", headers=headers, json={
    "name": "Ekstre Tedarikçi", "opening_balance": 50, "risk_limit": 0,
    "payment_term_days": 30, "is_active": True,
}))

database_path = os.environ["DATABASE_URL"].removeprefix("sqlite:///")
con = sqlite3.connect(database_path)
warehouse_id = int(con.execute(
    "SELECT id FROM warehouses WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)
).fetchone()[0])


def add_order(day, total, status, document_no):
    con.execute(
        """INSERT INTO orders(customer_id,order_date,subtotal,vat_total,grand_total,
        discount_percent,discount_amount,final_total,document_no,due_date,note,company_id,
        warehouse_id,branch_id,status,payment_method,paid_amount,payment_term)
        VALUES(?,?,?,0,?,0,0,?,?,?,NULL,?,?,NULL,?,'credit',0,'standard')""",
        (customer["id"], day, total, total, total, document_no, day, company_id,
         warehouse_id, status),
    )


def add_purchase(day, total, status, document_no):
    con.execute(
        """INSERT INTO purchases(supplier_id,purchase_date,subtotal,vat_total,grand_total,
        discount_percent,discount_amount,final_total,document_no,due_date,note,company_id,
        warehouse_id,branch_id,status,payment_method,paid_amount)
        VALUES(?,?,?,0,?,0,0,?,?,?,NULL,?,?,NULL,?,'credit',0)""",
        (supplier["id"], day, total, total, total, document_no, day, company_id,
         warehouse_id, status),
    )


def add_payment(entity_type, entity_id, day, amount):
    con.execute(
        """INSERT INTO payments(entity_type,entity_id,amount,payment_date,note,
        company_id,payment_method) VALUES(?,?,?,?,'',?,'cash')""",
        (entity_type, entity_id, amount, day, company_id),
    )


# Pencere: 2026-07. Haziran hareketleri devire, ağustos hareketi pencere
# dışına, taslak belge ise tamamen dışarıda kalmalıdır.
add_order("2026-06-15", 200, "completed", "EKS-001")
add_order("2026-07-05", 300, "completed", "EKS-002")
add_order("2026-07-20", 150, "draft", "EKS-003")
add_order("2026-08-01", 50, "completed", "EKS-004")
add_payment("customer", customer["id"], "2026-06-20", 50)
add_payment("customer", customer["id"], "2026-07-10", 120)
add_purchase("2026-06-15", 400, "completed", "EKA-001")
add_purchase("2026-07-08", 250, "completed", "EKA-002")
add_payment("supplier", supplier["id"], "2026-07-12", 90)
con.commit()
con.close()

period = {"date_from": "2026-07-01", "date_to": "2026-07-31"}
statement = ok(client.get(
    f"/api/customers/{customer['id']}/statement", headers=headers, params=period))

# Devir: açılış bakiyesi 100 + haziran satışı 200 - haziran tahsilatı 50.
assert statement["opening_balance"] == "250.00", statement
# Yürüyen bakiye her satırda ilerler: 250 + 300 = 550, 550 - 120 = 430.
assert [line["balance"] for line in statement["lines"]] == ["550.00", "430.00"], statement
assert [line["debit"] for line in statement["lines"]] == ["300.00", "0.00"], statement
assert [line["credit"] for line in statement["lines"]] == ["0.00", "120.00"], statement
assert statement["closing_balance"] == "430.00", statement
assert statement["closing_balance"] == statement["lines"][-1]["balance"], statement
assert statement["total_debit"] == "300.00", statement
assert statement["total_credit"] == "120.00", statement
assert statement["line_count"] == 2 and statement["truncated"] is False, statement
assert statement["entity"]["tax_number"] == "1234567890", statement["entity"]
# Taslak belge ne satırlarda ne de toplamlarda görünmelidir.
assert all(line["document_no"] != "EKS-003" for line in statement["lines"]), statement

# Tüm zaman aralığında kapanış, 360 ucunun bakiyesiyle örtüşmelidir.
whole = {"date_from": "2000-01-01", "date_to": "2100-01-01"}
full = ok(client.get(
    f"/api/customers/{customer['id']}/statement", headers=headers, params=whole))
detail = ok(client.get(f"/api/customers/{customer['id']}", headers=headers))
assert float(full["closing_balance"]) == float(detail["summary"]["current_balance"]), (
    full["closing_balance"], detail["summary"]["current_balance"])
assert full["closing_balance"] == full["lines"][-1]["balance"], full["lines"][-1]

# Hareketsiz dönem: 200 boş liste, açılış = kapanış.
empty = ok(client.get(f"/api/customers/{customer['id']}/statement", headers=headers,
                      params={"date_from": "2026-09-01", "date_to": "2026-09-30"}))
assert empty["lines"] == [], empty
assert empty["opening_balance"] == empty["closing_balance"] == "480.00", empty

# Tedarikçi aynı desenle çalışır: devir 50 + 400, kapanış 450 + 250 - 90.
supplier_statement = ok(client.get(
    f"/api/suppliers/{supplier['id']}/statement", headers=headers, params=period))
assert supplier_statement["opening_balance"] == "450.00", supplier_statement
assert supplier_statement["closing_balance"] == "610.00", supplier_statement
supplier_detail = ok(client.get(f"/api/suppliers/{supplier['id']}", headers=headers))
supplier_full = ok(client.get(
    f"/api/suppliers/{supplier['id']}/statement", headers=headers, params=whole))
assert float(supplier_full["closing_balance"]) == float(
    supplier_detail["summary"]["current_balance"]), supplier_full

# Ters aralık sınırda reddedilir.
invalid = client.get(f"/api/customers/{customer['id']}/statement", headers=headers,
                     params={"date_from": "2026-08-01", "date_to": "2026-07-01"})
assert invalid.status_code == 400, (invalid.status_code, invalid.text)

# Tenant izolasyonu: ikinci firmanın bağlamında A firmasının carisi görünmez.
other_company = ok(client.post("/api/companies", headers=headers,
                               json={"name": "İkinci Firma", "tax_number": "9999999999"}))
other_headers = dict(headers, **{"X-Company-ID": str(other_company["id"])})
leak = client.get(f"/api/customers/{customer['id']}/statement", headers=other_headers,
                  params=period)
assert leak.status_code == 404, (leak.status_code, leak.text)
supplier_leak = client.get(f"/api/suppliers/{supplier['id']}/statement",
                           headers=other_headers, params=period)
assert supplier_leak.status_code == 404, (supplier_leak.status_code, supplier_leak.text)
pdf_leak = client.get(f"/api/customers/{customer['id']}/statement.pdf",
                      headers=other_headers, params=period)
assert pdf_leak.status_code == 404, (pdf_leak.status_code, pdf_leak.text)

# PDF: hem müşteri hem tedarikçi için Unicode font gömülü bir belge üretir.
for url in (f"/api/customers/{customer['id']}/statement.pdf",
            f"/api/suppliers/{supplier['id']}/statement.pdf"):
    response = client.get(url, headers=headers, params=period)
    assert response.status_code == 200, (url, response.status_code, response.text)
    assert response.content.startswith(b"%PDF"), url
    assert b"DejaVuSans" in response.content, url

integrity = sqlite3.connect(database_path)
assert integrity.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
integrity.close()
client.close()
'''

    result = subprocess.run(
        [sys.executable, "-c", scenario],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_statement_table_body_uses_unicode_font() -> None:
    """Ekstre tablosunun GÖVDE hücreleri Türkçe taşıyan fontta basılmalıdır.

    ReportLab'in tablo varsayılanı Helvetica'dır ve o font Ş/ı/ğ/ş glifleri
    içermez (bkz. app/pdf_fonts.py). ``FONTNAME`` yalnızca başlık ve toplam
    satırlarına verilirse "Satış" / "Tahsilat" gibi satır etiketleri kutu
    olarak basılır — bu test o gerilemeyi yakalar.
    """
    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle

    from app.pdf_fonts import PDF_FONT, PDF_FONT_BOLD, register_pdf_fonts
    from app.routers.outputs import STATEMENT_TABLE_STYLE

    register_pdf_fonts()
    rows = [
        ["Tarih", "Açıklama", "Belge No", "Borç", "Alacak", "Bakiye"],
        ["01.07.2026", "Devir (açılış bakiyesi)", "", "", "", "250,00 TL"],
        ["05.07.2026", "Satış", "EKS-002", "300,00 TL", "", "550,00 TL"],
        ["10.07.2026", "Tahsilat (Nakit)", "", "", "120,00 TL", "430,00 TL"],
        ["31.07.2026", "Dönem toplamı", "", "300,00 TL", "120,00 TL", "430,00 TL"],
    ]
    table = Table(rows, repeatRows=1)
    table.setStyle(TableStyle(STATEMENT_TABLE_STYLE))
    table.wrap(174 * mm, 200 * mm)

    body_fonts = {
        table._cellStyles[row][column].fontname
        for row in (2, 3)
        for column in range(len(rows[0]))
    }
    assert body_fonts == {PDF_FONT}, body_fonts
    assert table._cellStyles[0][0].fontname == PDF_FONT_BOLD
    assert table._cellStyles[-1][0].fontname == PDF_FONT_BOLD
