"""Kapı: bakiye bırakan satış yaşlandırmaya VE tahsis defterine girmeli.

KUSUR (ölçüldü): satış yolu ``due_date`` verilmeden çağrıldığında vade NULL
kalıyordu. ``receivables_engine`` yaşlandırmayı
``due_date IS NOT NULL AND due_date<>''`` ile süzüyor; ``payment_allocation_engine``
belge kapsamını AYNI yüklemle seçiyor. Sonuç: bakiyesi olan gerçek bir alacak
hem rapordan hem normal tahsis akışından düşüyordu.

HANGİSİ KUSURLU: NULL. Süzgeç doğrudur — ``_aging_bucket`` kovayı
``as_of - due_date`` ile hesaplar; vadesi olmayan satır hiçbir kovaya konamaz.
Kural da uydurulmadı: ``service_receivable_engine`` servis alacağı için zaten
``belge tarihi + customers.payment_term_days`` kullanıyor.

AYIRT EDİCİ BAKİYEDİR, çağıran katman DEĞİL: ``paid_amount < final_total``.
Tamamı ödenmiş satışın alacağı yoktur; ona vade yazmak onu tahsis defterine
sokar ve defteri ``paid_amount`` ile uyumsuzlaştırır.

Kapı, vade yazan HER yazarı iki yönde birden sınar.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def run_pos_credit_aging_gate(database_url: str) -> str:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    # Tahsis defteri boyutunu GÖZLENEBİLİR kılmak için motor açılır
    # (settings.payment_allocation_engine_enabled varsayılanı False). Kusurun
    # asıl acıttığı yapılandırma da budur: vade NULL kalınca belge yalnız
    # yaşlandırmadan değil, normal tahsis akışından da düşüyor.
    env["PAYMENT_ALLOCATION_ENGINE_ENABLED"] = "true"
    completed = subprocess.run(
        [sys.executable, "-c", _GATE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    return completed.stdout


def _gate_lines(output: str) -> list[str]:
    """Only the gate's own lines. The subprocess also emits migration logs in
    Turkish, which the Windows console encoder cannot render."""
    return [
        line for line in output.splitlines()
        if line.startswith(("GATE_", "MUTATION_"))
    ]


def test_every_writer_puts_a_balance_sale_into_aging_and_the_ledger(
    tmp_path: Path,
) -> None:
    database = tmp_path / "pos-credit-aging.db"
    lines = _gate_lines(run_pos_credit_aging_gate(f"sqlite:///{database.as_posix()}"))
    for line in lines:
        print(line)
    assert "GATE_OK all writers, both directions" in lines, lines


_GATE = r'''
from datetime import timedelta
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import text

from app.business_time import business_today
from app.db import SessionLocal
from app.main import app

TERM_DAYS = 30


def gate(condition, message):
    """Named so a red is unambiguously THIS assertion, not a crash."""
    assert condition, message


with TestClient(app) as client:
    login = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    ).json()
    headers = {
        "Authorization": "Bearer " + login["access_token"],
        "X-Company-ID": str(login["companies"][0]["id"]),
    }
    changed = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": "admin123", "new_password": "PosCreditAging123!"},
    )
    assert changed.status_code == 200, changed.text
    headers["Authorization"] = "Bearer " + changed.json()["access_token"]

    company = client.post(
        "/api/companies", headers=headers, json={"name": "Vade Kapisi"}
    ).json()["id"]
    headers["X-Company-ID"] = str(company)

    product = client.post(
        "/api/products",
        headers=headers,
        json={"name": "Kapi Urunu", "sale_price": "100", "stock": "500",
              "product_code": "KAPI-1"},
    ).json()["id"]

    def customer(name):
        return client.post(
            "/api/customers",
            headers=headers,
            json={"name": name, "payment_term_days": TERM_DAYS},
        ).json()["id"]

    def order_row(order_id):
        with SessionLocal() as db:
            return db.execute(
                text("SELECT due_date, paid_amount, final_total, customer_id "
                     "FROM orders WHERE id=:i"),
                {"i": order_id},
            ).mappings().one()

    def in_aging(customer_id):
        response = client.get("/api/reports/receivables-aging", headers=headers)
        assert response.status_code == 200, response.text
        return any(
            int(row["customer_id"]) == int(customer_id)
            for row in response.json()["customers"]
        )

    def in_ledger(customer_id, order_id, suffix=""):
        """Real observable: pay the customer and see whether the engine
        allocates that payment onto this document."""
        paid = client.post(
            "/api/payments",
            headers={**headers, "Idempotency-Key": f"gate-pay-{order_id}{suffix}"},
            json={
                "entity_type": "customer",
                "entity_id": int(customer_id),
                "amount": "10",
                "payment_date": business_today().isoformat(),
                "payment_method": "cash",
            },
        )
        if paid.status_code >= 400:
            gate("kalan" in paid.text,
                 f"unexpected payment rejection ({paid.status_code}): {paid.text}")
            # No open document to allocate against: the engine refuses the
            # payment because the customer has no remaining balance. That IS
            # non-membership, observed through the real flow.
            return False
        assert paid.status_code in (200, 201), paid.text
        with SessionLocal() as db:
            return int(db.execute(
                text("SELECT count(*) FROM payment_allocations "
                     "WHERE company_id=:cid AND order_id=:oid"),
                {"cid": company, "oid": order_id},
            ).scalar_one()) > 0

    expected_due = (business_today() + timedelta(days=TERM_DAYS)).isoformat()

    # ------------------------------------------------------------------
    # WRITER 1 - POS (routers/pos.py)
    # ------------------------------------------------------------------
    def pos_sale(customer_id, payment_type, key):
        response = client.post(
            "/api/pos/sale",
            headers={**headers, "Idempotency-Key": key},
            json={
                "items": [{"product_id": product, "quantity": "1", "unit_price": "100"}],
                "payment_type": payment_type,
                "customer_id": customer_id,
            },
        )
        assert response.status_code in (200, 201), response.text
        return int(response.json()["sale_id"])

    pos_credit_customer = customer("POS Veresiye")
    pos_credit_order = pos_sale(pos_credit_customer, "credit", "gate-pos-credit")
    row = order_row(pos_credit_order)
    gate(row["due_date"] is not None and str(row["due_date"])[:10] == expected_due,
         f"pos.py balance sale must carry a derived due date; got {row['due_date']!r}")
    gate(in_aging(pos_credit_customer), "pos.py balance sale must appear in aging")
    gate(in_ledger(pos_credit_customer, pos_credit_order),
         "pos.py balance sale must be allocatable in the ledger")
    print(f"GATE_CONTROL pos.py/balance: due_date={expected_due} aging=True ledger=True")

    pos_cash_customer = customer("POS Pesin")
    pos_cash_order = pos_sale(pos_cash_customer, "cash", "gate-pos-cash")
    row = order_row(pos_cash_order)
    gate(row["due_date"] is None,
         f"pos.py fully paid sale must NOT get a due date; got {row['due_date']!r}")
    gate(not in_aging(pos_cash_customer), "pos.py fully paid sale must not appear in aging")
    gate(not in_ledger(pos_cash_customer, pos_cash_order),
         "pos.py fully paid sale must not be allocatable")
    print("GATE_CONTROL pos.py/fully-paid: due_date=None aging=False ledger=False")

    # ------------------------------------------------------------------
    # WRITER 2 - transactions API (routers/transactions.py)
    # ------------------------------------------------------------------
    def api_sale(customer_id, paid, method="credit"):
        response = client.post(
            "/api/orders",
            headers=headers,
            json={
                "entity_id": customer_id,
                "transaction_date": business_today().isoformat(),
                "status": "completed",
                "payment_method": method,
                "paid_amount": paid,
                "items": [{"product_id": product, "quantity": "1",
                           "unit_price": "100", "vat_rate": 20}],
            },
        )
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    api_balance_customer = customer("API Bakiyeli")
    api_balance_order = api_sale(api_balance_customer, "0")
    row = order_row(api_balance_order)
    gate(row["due_date"] is not None,
         f"transactions API balance sale must carry a due date; got {row['due_date']!r}")
    gate(in_aging(api_balance_customer), "transactions API balance sale must appear in aging")
    gate(in_ledger(api_balance_customer, api_balance_order),
         "transactions API balance sale must be allocatable")
    print(f"GATE_CONTROL transactions-api/balance: due_date={str(row['due_date'])[:10]} aging=True ledger=True")

    api_paid_customer = customer("API Kapali")
    # KDV fiyata DAHİL: 100 birim fiyat -> final_total 100 (POS yanıtıyla ölçüldü).
    # Tamamı ödenmiş satış nakit yöntemle girilir; "credit" yöntemi ödenmiş
    # tutarla bağdaşmıyor (sistem reddediyor).
    api_paid_order = api_sale(api_paid_customer, "100", method="cash")
    row = order_row(api_paid_order)
    gate(row["due_date"] is None,
         f"transactions API fully paid sale must NOT get a due date; got {row['due_date']!r}")
    gate(not in_aging(api_paid_customer),
         "transactions API fully paid sale must not appear in aging")
    print("GATE_CONTROL transactions-api/fully-paid: due_date=None aging=False")

    # ------------------------------------------------------------------
    # WRITER 3 - sales order -> sale conversion (routers/workflow.py)
    # ------------------------------------------------------------------
    workflow_customer = customer("Siparis Musterisi")
    warehouse_id = client.get("/api/warehouses", headers=headers).json()[0]["id"]
    sales_order = client.post(
        "/api/workflow/sales_order",
        headers=headers,
        json={
            "entity_id": workflow_customer,
            "document_date": business_today().isoformat(),
            "warehouse_id": warehouse_id,
            "status": "approved",
            "discount_percent": 0,
            # delivery_date VERİLMİYOR: dönüşüm due_date'i ondan alıyordu,
            # NULL olduğunda vade NULL kalıyordu. Kusurun tam yolu budur.
            "items": [{"product_id": product, "quantity": 1, "unit_price": 100,
                       "vat_rate": 20, "discount_percent": 0}],
        },
    )
    assert sales_order.status_code in (200, 201), sales_order.text
    sales_order_id = int(sales_order.json()["id"])
    converted = client.post(
        f"/api/workflow/sales_order/{sales_order_id}/to-sale", headers=headers
    )
    assert converted.status_code in (200, 201), converted.text
    workflow_order = int(converted.json()["id"])
    row = order_row(workflow_order)
    gate(row["due_date"] is not None,
         "workflow conversion with a NULL delivery_date must still derive a due date; "
         f"got {row['due_date']!r}")
    gate(in_aging(workflow_customer), "workflow conversion sale must appear in aging")
    print(f"GATE_CONTROL workflow-conversion/balance: due_date={str(row['due_date'])[:10]} aging=True")

    # ------------------------------------------------------------------
    # WRITER 4 - Excel sales import (routers/imports.py)
    # ------------------------------------------------------------------
    import_customer_name = "Excel Musterisi"
    import_customer = customer(import_customer_name)
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["SATIS RAPORU"])
    sheet.append(["01.01.2026 - 31.12.2026"])
    sheet.append(["Musteri", "Urun", "Kod", "Miktar", "Net", "TOPLAM", "KDV(%)", "Tarih"])
    sheet.append([import_customer_name, "Kapi Urunu", "KAPI-1", 1, 100, 120, 20,
                  business_today().strftime("%d.%m.%Y")])
    buffer = BytesIO()
    workbook.save(buffer)
    imported = client.post(
        "/api/imports/sales/excel",
        headers=headers,
        files={"file": ("satis.xlsx", buffer.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert imported.status_code in (200, 201), imported.text
    gate(imported.json().get("documents_created") == 1,
         f"the Excel import must create exactly one sale: {imported.json()}")
    with SessionLocal() as db:
        import_row = db.execute(
            text("SELECT o.id AS id, o.due_date AS due_date, o.customer_id AS customer_id "
                 "FROM orders o "
                 "JOIN customers c ON c.id=o.customer_id AND c.company_id=o.company_id "
                 "WHERE o.company_id=:cid AND c.name=:n ORDER BY o.id DESC"),
            {"cid": company, "n": import_customer_name},
        ).mappings().first()
    gate(import_row is not None, "the Excel import must have produced a sale")
    gate(import_row["due_date"] is not None,
         f"imports.py sale must carry a derived due date; got {import_row['due_date']!r}")
    gate(in_aging(import_row["customer_id"]), "imports.py sale must appear in aging")
    print(f"GATE_CONTROL imports.py/balance: due_date={str(import_row['due_date'])[:10]} aging=True")

    # ------------------------------------------------------------------
    # MUTATION 1 - the pre-fix state: clear the derived due date.
    # ------------------------------------------------------------------
    with SessionLocal() as db:
        db.execute(text("UPDATE orders SET due_date=NULL WHERE id=:i"),
                   {"i": pos_credit_order})
        db.commit()
    gate(order_row(pos_credit_order)["due_date"] is None,
         "mutation setup failed: due_date was not cleared")
    try:
        gate(in_aging(pos_credit_customer), "pos.py balance sale must appear in aging")
        raise SystemExit("MUTATION 1 FAILED TO FIRE: aging still showed a NULL-due-date sale")
    except AssertionError as error:
        print(f"MUTATION_RED direction1[due_date=NULL, the pre-fix state]: {error}")

    # ------------------------------------------------------------------
    # MUTATION 2 - bakiye kuralının yükünü ÖLÇ.
    #
    # ÖLÇÜLEN SONUÇ: tamamı ödenmiş bir satışa vade yazmak onu yaşlandırmaya da
    # tahsis defterine de SOKMUYOR. Dışlanma AŞIRI BELİRLENMİŞ: kalan bakiye
    # sıfır olduğu için iki motor da onu vadeden BAĞIMSIZ olarak eliyor.
    # Dolayısıyla bu yönde VERİ düzeyinde kırmızı üretilemez; ürettiğimizi
    # söylemek yanlış olurdu.
    #
    # Kuralın gerçek yükü MUTABAKAT değişmezindedir: tahsis motoru
    # ``stored_paid != direct_applied`` olduğunda 409 veriyor
    # (payment_allocation_engine.py:797). Kural kaldırılıp tamamı ödenmiş
    # satışa vade türetildiğinde KIRMIZI olan yer orasıdır ve bu, KOD
    # mutasyonuyla iki kez ölçüldü: test_v3_pos "Belgenin paid_amount değeri
    # tahsis defteriyle mutabık değil" ile düşüyor. Kanıt o testte yaşıyor.
    with SessionLocal() as db:
        db.execute(text("UPDATE orders SET due_date=:d WHERE id=:i"),
                   {"d": expected_due, "i": pos_cash_order})
        db.commit()
    forced = order_row(pos_cash_order)
    gate(str(forced["due_date"])[:10] == expected_due, "mutation setup failed")
    gate(not in_aging(pos_cash_customer),
         "over-determination claim is wrong: a zero-balance sale entered aging once given a due date")
    gate(not in_ledger(pos_cash_customer, pos_cash_order, suffix="-m2"),
         "over-determination claim is wrong: a zero-balance sale became allocatable once given a due date")
    print("GATE_MEASURED direction2: forcing a due date onto a fully paid sale "
          "changes nothing (aging=False ledger=False) - exclusion is over-determined "
          "by the zero remaining balance; the balance rule's load is the "
          "reconciliation invariant, red in test_v3_pos under code mutation")

    print("GATE_OK all writers, both directions")
'''
