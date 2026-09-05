from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import entity_detail as entity_detail_module
from app.core_schema import customers, metadata, orders, purchases, suppliers
from app.document_engine import SALES_IMPORT_NOTE
from app import receivables_engine
from app.payment_allocation_engine import _lock_documents
from app.receivables_engine import calculate_receivables
from app.routers.customers import list_customers
from app.statement import build_statement


def _request_with_company(cid: int):
    return SimpleNamespace(state=SimpleNamespace(company_id=cid))


def _create_auxiliary_tables(db: Session) -> None:
    """core_schema dışında kalan alacak/tahsis tablolarını kurar.

    ``entity_detail`` bunları koşulsuz sorguluyor; seed eden her testin kurması
    gerekiyor, yoksa "no such table" ile düşüyor.

    ``producer_receipts`` de aynı sebeple BURADA: D2'den beri tedarikçi
    bakiyesi kesilmiş müstahsil makbuzunu BORÇ olarak sayıyor
    (``statement.py:_makbuz_borcu``) ve o sorgu da KOŞULSUZDUR. Sorguyu
    "tablo varsa" diye sarmalamak, tablo bir gün gerçekten eksik olduğunda
    borcu SESSİZCE sıfırlardı — eksik tablo gürültülü düşmelidir.
    """
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS producer_receipts(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              company_id INTEGER NOT NULL,
              supplier_id INTEGER NOT NULL,
              receipt_no TEXT,
              issued_at TEXT,
              gross_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
              withholding_total NUMERIC(18,2) NOT NULL DEFAULT 0,
              social_security_total NUMERIC(18,2) NOT NULL DEFAULT 0,
              net_payable NUMERIC(18,2) NOT NULL DEFAULT 0,
              advance_applied_total NUMERIC(18,2) NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'draft'
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS receivable_charge_documents(
                id INTEGER PRIMARY KEY,
                customer_id INTEGER,
                company_id INTEGER,
                charge_type TEXT,
                gross_amount NUMERIC(18,2) DEFAULT 0,
                reversal_of INTEGER,
                reversal_of_document_id INTEGER,
                status TEXT,
                posted_at TEXT,
                due_date_snapshot TEXT,
                period_end TEXT,
                revision_no INTEGER DEFAULT 1,
                work_order_id INTEGER
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS work_orders(
                id INTEGER PRIMARY KEY,
                company_id INTEGER,
                work_order_no TEXT
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS payment_allocations(
                id INTEGER PRIMARY KEY,
                receivable_charge_id INTEGER,
                order_id INTEGER,
                company_id INTEGER,
                reversal_of_allocation_id INTEGER,
                amount NUMERIC(18,2) DEFAULT 0,
                effective_date TEXT
            )
            """
        )
    )
    db.execute(
        text(
            """CREATE TABLE IF NOT EXISTS receivable_legacy_residuals(
            id INTEGER PRIMARY KEY,
            company_id INTEGER,
            order_id INTEGER,
            amount NUMERIC(18,2),
            status TEXT
            )"""
        )
    )


def _seed_customer_data() -> tuple[Session, int]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    with Session(engine) as db:
        _create_auxiliary_tables(db)
        result = db.execute(
            customers.insert().values(
                name="Test Müşteri",
                opening_balance=Decimal("0"),
                company_id=1,
            )
        )
        customer_id = int(result.inserted_primary_key[0])
        db.execute(
            orders.insert(),
            [
                {
                    "company_id": 1,
                    "customer_id": customer_id,
                    "order_date": "2026-07-01",
                    "final_total": Decimal("125.00"),
                    "due_date": "2026-07-10",
                    "status": "completed",
                    "document_no": "S-K",
                    "note": None,
                },
                {
                    "company_id": 1,
                    "customer_id": customer_id,
                    "order_date": "2026-07-02",
                    "final_total": Decimal("250.00"),
                    "due_date": "2026-07-11",
                    "status": "draft",
                    "note": "Normal Taslak",
                    "document_no": "S-D1",
                },
                {
                    "company_id": 1,
                    "customer_id": customer_id,
                    "order_date": "2026-07-03",
                    "final_total": Decimal("400.00"),
                    "due_date": "2026-07-12",
                    "status": "draft",
                    "note": SALES_IMPORT_NOTE,
                    "document_no": "S-I1",
                },
                {
                    "company_id": 1,
                    "customer_id": customer_id,
                    "order_date": "2026-07-04",
                    "final_total": Decimal("999.00"),
                    "due_date": "2026-07-13",
                    "status": "cancelled",
                    "note": "Cancelled",
                    "document_no": "S-C1",
                },
            ],
        )
        db.commit()
    return Session(engine), customer_id


def _patch_detail_side_effects(monkeypatch):
    monkeypatch.setattr(entity_detail_module, "list_notes", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(entity_detail_module, "list_contacts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(entity_detail_module, "list_tasks", lambda *_args, **_kwargs: [])
    # Charge documents are calculated separately and should not affect this focused test.
    monkeypatch.setattr(entity_detail_module, "_charge_documents", lambda *_args, **_kwargs: [])


def test_entity_detail_counts_imported_draft_in_summary_and_excludes_draft_cancelled(monkeypatch):
    db, customer_id = _seed_customer_data()
    _patch_detail_side_effects(monkeypatch)

    try:
        result = entity_detail_module.entity_detail(
            "customer", customer_id, _request_with_company(1), db
        )
    finally:
        db.close()

    assert result["summary"]["document_total"] == Decimal("525.00")
    assert result["summary"]["current_balance"] == Decimal("525.00")
    assert result["summary"]["document_count"] == 2


def test_entity_detail_balance_and_document_total_use_same_accounting_documents(monkeypatch):
    db, customer_id = _seed_customer_data()
    _patch_detail_side_effects(monkeypatch)
    try:
        result = entity_detail_module.entity_detail(
            "customer", customer_id, _request_with_company(1), db
        )
    finally:
        db.close()

    assert result["summary"]["current_balance"] == result["summary"]["document_total"]


def _seed_scrambled_order_dates() -> tuple[Session, int]:
    """Kayıt sırası tarih sırasından FARKLI bir cari üretir.

    İçe aktarılan geçmişte durum tam olarak budur: satırlar Excel'deki sırayla
    girer, o sıra da tarih sırası değildir. Burada en yeni satış (2026-07-01) en
    küçük id'yi alır, yani ``ORDER BY id DESC`` onu listenin en ALTINA atardı.
    """
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    with Session(engine) as db:
        _create_auxiliary_tables(db)
        result = db.execute(
            customers.insert().values(
                name="İçe Aktarılmış Cari",
                opening_balance=Decimal("0"),
                company_id=1,
            )
        )
        customer_id = int(result.inserted_primary_key[0])
        db.execute(
            orders.insert(),
            [
                {
                    "company_id": 1,
                    "customer_id": customer_id,
                    "order_date": order_date,
                    "final_total": Decimal("100.00"),
                    "status": "completed",
                    "document_no": document_no,
                    "note": None,
                }
                for order_date, document_no in (
                    ("2026-07-01", "S-YENI"),
                    ("2025-06-18", "S-ESKI"),
                    ("2026-05-14", "S-ORTA"),
                )
            ],
        )
        db.commit()
    return Session(engine), customer_id


def test_entity_detail_lists_documents_newest_date_first(monkeypatch):
    db, customer_id = _seed_scrambled_order_dates()
    _patch_detail_side_effects(monkeypatch)
    try:
        result = entity_detail_module.entity_detail(
            "customer", customer_id, _request_with_company(1), db
        )
    finally:
        db.close()

    # Kart, BizimHesap gibi en yeni satışı en üstte göstermeli. id sırasına
    # dönülürse bu liste ["S-ORTA","S-ESKI","S-YENI"] olur ve test kırılır.
    assert [row["document_no"] for row in result["documents"]] == [
        "S-YENI",
        "S-ORTA",
        "S-ESKI",
    ]


def test_imported_draft_is_consistent_in_aging_statement_and_customer_list():
    db, customer_id = _seed_customer_data()
    try:
        receivables = calculate_receivables(db, 1, date(2026, 7, 31))
        statement = build_statement(
            db,
            1,
            "customer",
            customer_id,
            date_from=date(2026, 7, 1),
            date_to=date(2026, 7, 31),
        )
        customers_list = list_customers(
            _request_with_company(1),
            q="",
            sort="name_asc",
            active="all",
            limit=500,
            db=db,
        )
    finally:
        db.close()

    assert {item.document_no for item in receivables} == {"S-K", "S-I1"}
    assert [line.document_no for line in statement.lines] == ["S-K", "S-I1"]
    assert statement.closing_balance == Decimal("525.00")
    assert customers_list[0]["order_count"] == 2
    assert customers_list[0]["last_activity"] == "2026-07-03"


def test_charge_document_preview_excludes_fully_applied_charge():
    db, customer_id = _seed_customer_data()
    try:
        db.execute(
            text(
                """INSERT INTO receivable_charge_documents(
                id,customer_id,company_id,charge_type,gross_amount,status,
                posted_at,due_date_snapshot,period_end,revision_no)
                VALUES(1,:customer_id,1,'late_fee',100,'posted',
                '2026-07-01','2026-07-01','2026-07-01',1)"""
            ),
            {"customer_id": customer_id},
        )
        db.execute(
            text(
                """INSERT INTO payment_allocations(
                id,receivable_charge_id,company_id,amount,effective_date)
                VALUES(1,1,1,100,'2026-07-02')"""
            )
        )
        db.commit()

        documents = entity_detail_module._charge_documents(
            db, 1, customer_id, date(2026, 7, 31)
        )
    finally:
        db.close()

    assert documents == []


def test_imported_draft_accepts_ledger_allocation_and_reduces_aging(monkeypatch):
    db, customer_id = _seed_customer_data()
    try:
        imported_id = int(
            db.execute(
                text(
                    "SELECT id FROM orders WHERE company_id=1 AND document_no='S-I1'"
                )
            ).scalar_one()
        )
        locked = _lock_documents(db, 1, customer_id, imported_id)
        assert imported_id in {int(row["id"]) for row in locked}

        db.execute(
            text(
                """INSERT INTO payment_allocations(
                id,order_id,company_id,amount,effective_date)
                VALUES(1,:order_id,1,100,'2026-07-20')"""
            ),
            {"order_id": imported_id},
        )
        db.commit()
        monkeypatch.setattr(
            receivables_engine.settings, "payment_allocation_engine_enabled", True
        )

        documents = calculate_receivables(db, 1, date(2026, 7, 31))
    finally:
        db.close()

    imported = next(item for item in documents if item.id == imported_id)
    assert imported.total == Decimal("400.00")
    assert imported.applied == Decimal("100.00")
    assert imported.remaining == Decimal("300.00")


def test_sales_import_marker_does_not_include_supplier_draft(monkeypatch):
    db, _customer_id = _seed_customer_data()
    _patch_detail_side_effects(monkeypatch)
    try:
        supplier_id = int(
            db.execute(
                suppliers.insert().values(
                    name="Test Tedarikçi",
                    opening_balance=Decimal("0"),
                    company_id=1,
                )
            ).inserted_primary_key[0]
        )
        db.execute(
            purchases.insert(),
            [
                {
                    "company_id": 1,
                    "supplier_id": supplier_id,
                    "purchase_date": "2026-07-01",
                    "final_total": Decimal("100.00"),
                    "status": "completed",
                    "document_no": "A-K",
                    "note": None,
                },
                {
                    "company_id": 1,
                    "supplier_id": supplier_id,
                    "purchase_date": "2026-07-02",
                    "final_total": Decimal("900.00"),
                    "status": "draft",
                    "document_no": "A-D",
                    "note": SALES_IMPORT_NOTE,
                },
            ],
        )
        db.commit()

        detail = entity_detail_module.entity_detail(
            "supplier", supplier_id, _request_with_company(1), db
        )
        statement = build_statement(
            db,
            1,
            "supplier",
            supplier_id,
            date_from=date(2026, 7, 1),
            date_to=date(2026, 7, 31),
        )
    finally:
        db.close()

    assert detail["summary"]["document_total"] == Decimal("100.00")
    assert detail["summary"]["document_count"] == 1
    assert statement.closing_balance == Decimal("100.00")
    assert [line.document_no for line in statement.lines] == ["A-K"]
