from __future__ import annotations

from sqlalchemy import (
    Column,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.schema import CreateColumn

from .core_schema import (
    _backfill_company_id_from_existing_company,
    _make_company_id_required,
)


MONEY = Numeric(18, 2)
QUANTITY = Numeric(18, 4)
metadata = MetaData()

sales_orders = Table(
    "sales_orders",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, nullable=False, index=True),
    Column("customer_id", Integer, nullable=False),
    Column("document_no", String),
    Column("order_date", String, nullable=False),
    Column("delivery_date", String),
    Column("status", String, nullable=False, server_default="draft"),
    Column("warehouse_id", Integer),
    Column("note", String),
    Column("subtotal", MONEY, server_default="0"),
    Column("vat_total", MONEY, server_default="0"),
    Column("discount_percent", MONEY, server_default="0"),
    Column("discount_amount", MONEY, server_default="0"),
    Column("grand_total", MONEY, server_default="0"),
    Column("converted_type", String),
    Column("converted_id", Integer),
    # Çocuğun bileşik yabancı anahtarının hedefi. ``id`` tek başına zaten
    # tekil; çift, çocuğun aynı referansta company_id'yi de adlandırabilmesi
    # için var.
    UniqueConstraint("company_id", "id", name="uq_sales_orders_company_id"),
)

sales_order_items = Table(
    "sales_order_items",
    metadata,
    Column("id", Integer, primary_key=True),
    # Satırın kendi kiracısı. Ebeveynden türetilebilir olması yetmez: türetme
    # her sorgunun ebeveyni JOIN'leyip filtrelemesine bağlıdır ve bu bir
    # konvansiyondur, garanti değil.
    Column("company_id", Integer, nullable=False),
    Column("sales_order_id", Integer, nullable=False, index=True),
    Column("product_id", Integer),
    Column("product_name", String, nullable=False),
    Column("quantity", QUANTITY, nullable=False),
    Column("unit_price", MONEY, nullable=False),
    Column("vat_rate", MONEY, server_default="20"),
    Column("discount_percent", MONEY, server_default="0"),
    Column("discount_amount", MONEY, server_default="0"),
    Column("line_subtotal", MONEY, server_default="0"),
    Column("line_vat", MONEY, server_default="0"),
    Column("line_total", MONEY, nullable=False),
    # Garantinin kendisi: çift ebeveynde VAR olmak zorunda, yani satır başka
    # bir firmanın siparişine bağlanamaz. Veritabanı zorluyor (db.py'de
    # PRAGMA foreign_keys=ON açık olduğu için SQLite'ta da).
    ForeignKeyConstraint(
        ["company_id", "sales_order_id"],
        ["sales_orders.company_id", "sales_orders.id"],
        name="fk_sales_order_items_order_same_company",
    ),
    Index("ix_sales_order_items_company_order", "company_id", "sales_order_id"),
)

delivery_notes = Table(
    "delivery_notes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, nullable=False, index=True),
    Column("customer_id", Integer, nullable=False),
    Column("document_no", String),
    Column("delivery_date", String, nullable=False),
    Column("status", String, nullable=False, server_default="draft"),
    Column("warehouse_id", Integer, nullable=False),
    Column("note", String),
    Column("total", MONEY, server_default="0"),
    Column("source_type", String),
    Column("source_id", Integer),
    Column("converted_order_id", Integer),
    UniqueConstraint("company_id", "id", name="uq_delivery_notes_company_id"),
)

delivery_note_items = Table(
    "delivery_note_items",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, nullable=False),
    Column("delivery_note_id", Integer, nullable=False, index=True),
    Column("product_id", Integer),
    Column("product_name", String, nullable=False),
    Column("quantity", QUANTITY, nullable=False),
    Column("unit_price", MONEY, server_default="0"),
    Column("line_total", MONEY, server_default="0"),
    ForeignKeyConstraint(
        ["company_id", "delivery_note_id"],
        ["delivery_notes.company_id", "delivery_notes.id"],
        name="fk_delivery_note_items_note_same_company",
    ),
    Index("ix_delivery_note_items_company_note", "company_id", "delivery_note_id"),
)


def _add(conn: Connection, table_name: str, column: Column) -> None:
    columns = {item["name"] for item in inspect(conn).get_columns(table_name)}
    if column.name in columns:
        return

    preparer = conn.dialect.identifier_preparer
    table_sql = preparer.quote(table_name)
    column_sql = str(CreateColumn(column).compile(dialect=conn.dialect))
    conn.execute(text(f"ALTER TABLE {table_sql} ADD COLUMN {column_sql}"))


def _add_legacy_columns(conn: Connection) -> None:
    inspector = inspect(conn)
    legacy_columns = {
        "quotes": (
            Column("company_id", Integer),
            Column("document_no", String),
            Column("warehouse_id", Integer),
            Column("subtotal", MONEY, server_default="0"),
            Column("vat_total", MONEY, server_default="0"),
            Column("discount_percent", MONEY, server_default="0"),
            Column("discount_amount", MONEY, server_default="0"),
            Column("grand_total", MONEY, server_default="0"),
            Column("converted_type", String),
            Column("converted_id", Integer),
        ),
        "quote_items": (
            Column("discount_percent", MONEY, server_default="0"),
            Column("discount_amount", MONEY, server_default="0"),
            Column("line_subtotal", MONEY, server_default="0"),
            Column("line_vat", MONEY, server_default="0"),
        ),
        "returns": (
            Column("company_id", Integer),
            Column("warehouse_id", Integer),
            Column("status", String, server_default="completed"),
            Column("source_type", String),
            Column("source_id", Integer),
            Column("subtotal", MONEY, server_default="0"),
            Column("vat_total", MONEY, server_default="0"),
        ),
        "return_items": (
            Column("vat_rate", MONEY, server_default="20"),
            Column("line_subtotal", MONEY, server_default="0"),
            Column("line_vat", MONEY, server_default="0"),
        ),
    }

    for table_name, columns in legacy_columns.items():
        if not inspector.has_table(table_name):
            continue
        for column in columns:
            _add(conn, table_name, column)
        if table_name in {"quotes", "returns"}:
            quoted_table = conn.dialect.identifier_preparer.quote(table_name)
            _backfill_company_id_from_existing_company(conn, table_name)
            _make_company_id_required(conn, table_name)
            index_name = f"idx_{table_name}_company"
            quoted_index = conn.dialect.identifier_preparer.quote(index_name)
            conn.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS {quoted_index} "
                    f"ON {quoted_table} (company_id)"
                )
            )


def initialize_workflow(engine: Engine) -> None:
    """Create workflow tables and extend legacy tables using portable SQLAlchemy DDL."""
    with engine.begin() as conn:
        _add_legacy_columns(conn)
        metadata.create_all(conn, checkfirst=True)
