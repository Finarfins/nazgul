from __future__ import annotations

import re

from sqlalchemy import inspect, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .core_schema import document_sequences

DOCUMENT_STATUSES = {"draft", "pending", "approved", "completed", "cancelled"}
PAYMENT_METHODS = {"credit", "cash", "card", "bank_transfer", "check", "promissory_note", "mixed"}
STOCK_STATUSES = {"approved", "completed"}
SALES_IMPORT_NOTE = "BizimHesap içe aktarma"
# Ödeme vadesi (payment term) is orthogonal to payment_method: it records *when*
# a sale is collected, not *how*. ``PESIN`` (default) preserves the legacy
# behaviour; ``HARMAN_VADELI`` defers collection to the harvest season and
# therefore carries a due_date.
PAYMENT_TERMS = {"PESIN", "HARMAN_VADELI"}
DEFAULT_PAYMENT_TERM = "PESIN"

# Table names are used as SQL identifiers, so they must never come from user
# input.  All call sites use this closed set through internal router configs.
DOCUMENT_TABLES = {
    "orders",
    "purchases",
    "quotes",
    "sales_orders",
    "delivery_notes",
    "returns",
    "work_orders",
    # Müstahsil makbuzu (0070). Numara TASLAKTA atanmaz; yalnız
    # `/producer-receipts/{id}/issue` bu seriden çeker (önek "MM").
    "producer_receipts",
}
DOCUMENT_NUMBER_COLUMNS = {
    "work_orders": "work_order_no",
    # Bu tablonun numara sütunu `document_no` DEĞİL: makbuzun kağıt üstündeki
    # adı "makbuz no"dur ve sütun adını belgeye uydurmak, tekrar denetiminin
    # (`next_document_no` içindeki LOWER(...) sorgusu) DOĞRU sütuna bakmasını
    # sağlar. Burada bildirilmeseydi sorgu olmayan `document_no`ya bakardı.
    "producer_receipts": "receipt_no",
}
_PREFIX_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,31}$")


def accounting_document_status_sql(
    status_column: str = "status", note_column: str = "note"
) -> str:
    """Return SQL predicate for documents that count as accounting totals.

    Rule:
    - cancelled never counts,
    - normal draft does not count,
    - imported draft counts (BizimHesap import marker in note).

    The note match is string-based and thus intentionally centralized here so the
    same fallback can be changed in one location if a dedicated schema flag is
    added later.
    """
    return (
        f"""(COALESCE({status_column}, 'completed') NOT IN ('cancelled')
        AND (
          COALESCE({status_column}, 'completed') <> 'draft'
          OR COALESCE(TRIM({note_column}),'') = :sales_import_note
        ))"""
    )


def initialize_document_engine(engine: Engine) -> None:
    with engine.begin() as conn:
        inspector = inspect(conn)
        for table in ("orders", "purchases"):
            if not inspector.has_table(table):
                continue
            cols = {x["name"] for x in inspector.get_columns(table)}
            additions = {
                "status": "TEXT DEFAULT 'completed'",
                "payment_method": "TEXT DEFAULT 'credit'",
                "paid_amount": "NUMERIC(18,2) DEFAULT 0",
            }
            for name, ddl in additions.items():
                if name not in cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
            conn.execute(text(f"UPDATE {table} SET status='completed' WHERE status IS NULL OR status=''"))
            conn.execute(text(f"UPDATE {table} SET payment_method='credit' WHERE payment_method IS NULL OR payment_method=''"))
            conn.execute(text(f"UPDATE {table} SET paid_amount=0 WHERE paid_amount IS NULL"))

        inspector = inspect(conn)
        for table in ("order_items", "purchase_items"):
            if not inspector.has_table(table):
                continue
            cols = {x["name"] for x in inspector.get_columns(table)}
            if "discount_percent" not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN discount_percent NUMERIC(18,2) DEFAULT 0"))
            if "discount_amount" not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN discount_amount NUMERIC(18,2) DEFAULT 0"))

        if inspector.has_table("payments"):
            cols = {x["name"] for x in inspector.get_columns("payments")}
            for name, ddl in {
                "payment_method": "TEXT DEFAULT 'cash'",
                "reference_type": "TEXT",
                "reference_id": "INTEGER",
            }.items():
                if name not in cols:
                    conn.execute(text(f"ALTER TABLE payments ADD COLUMN {name} {ddl}"))


def _validate_document_identity(table: str, prefix: str) -> None:
    if table not in DOCUMENT_TABLES:
        raise ValueError(f"Belge numarası için desteklenmeyen tablo: {table!r}")
    if not _PREFIX_RE.fullmatch(prefix):
        raise ValueError("Belge numarası öneki yalnızca A-Z, 0-9, _ ve - içerebilir")


def _insert_sequence_if_missing(
    db: Session,
    company_id: int,
    sequence_key: str,
    seed: int,
) -> None:
    values = {
        "company_id": company_id,
        "sequence_key": sequence_key,
        "current_value": seed,
    }
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(document_sequences).values(**values).on_conflict_do_nothing(
            index_elements=["company_id", "sequence_key"]
        )
        db.execute(statement)
        return
    if dialect == "sqlite":
        statement = sqlite_insert(document_sequences).values(**values).on_conflict_do_nothing(
            index_elements=["company_id", "sequence_key"]
        )
        db.execute(statement)
        return

    # Defensive fallback for another SQLAlchemy dialect.  The savepoint prevents
    # a concurrent insert from rolling back the caller's complete transaction.
    try:
        with db.begin_nested():
            db.execute(document_sequences.insert().values(**values))
    except IntegrityError:
        pass


def next_document_no(db: Session, table: str, company_id: int, prefix: str) -> str:
    """Return a concurrency-safe, monotonically increasing document number.

    The old implementation used ``MAX(id) + 1`` directly. Two concurrent
    requests could therefore produce the same number.  The counter row is now
    updated atomically with ``UPDATE ... RETURNING`` and is isolated per company,
    table and prefix.
    """
    _validate_document_identity(table, prefix)
    bind = db.get_bind()
    quoted_table = bind.dialect.identifier_preparer.quote(table)
    number_column = DOCUMENT_NUMBER_COLUMNS.get(table, "document_no")
    quoted_number_column = bind.dialect.identifier_preparer.quote(number_column)
    seed = int(
        db.execute(
            text(f"SELECT COALESCE(MAX(id), 0) FROM {quoted_table} WHERE company_id=:cid"),
            {"cid": company_id},
        ).scalar_one()
    )
    sequence_key = f"{table}:{prefix}"
    _insert_sequence_if_missing(db, company_id, sequence_key, seed)

    # Existing/imported installations may already contain a manually assigned
    # generated-looking number above MAX(id). Keep advancing until an unused
    # number is found. In normal operation this loop executes exactly once.
    for _ in range(10_000):
        value = db.execute(
            update(document_sequences)
            .where(
                document_sequences.c.company_id == company_id,
                document_sequences.c.sequence_key == sequence_key,
            )
            .values(current_value=document_sequences.c.current_value + 1)
            .returning(document_sequences.c.current_value)
        ).scalar_one()
        candidate = f"{prefix}-{int(value):06d}"
        exists = db.execute(
            text(
                f"SELECT 1 FROM {quoted_table} "
                f"WHERE company_id=:cid AND LOWER({quoted_number_column})="
                "LOWER(:document_no) LIMIT 1"
            ),
            {"cid": company_id, "document_no": candidate},
        ).first()
        if not exists:
            return candidate

    raise RuntimeError("Belge numarası üretilemedi; sıra sayacı olağandışı biçimde çakışıyor")
