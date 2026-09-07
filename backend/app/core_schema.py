from __future__ import annotations

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.schema import CreateColumn

MONEY = Numeric(18, 2)
QUANTITY = Numeric(18, 4)
metadata = MetaData()

customers = Table(
    "customers",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(200), nullable=False),
    Column("owner_name", String(160)),
    Column("phone", String(60)),
    Column("email", String(200)),
    Column("address", Text),
    Column("tax_number", String(60)),
    Column("opening_balance", MONEY, nullable=False, server_default="0"),
    Column("risk_limit", MONEY, nullable=False, server_default="0"),
    Column("payment_term_days", Integer, nullable=False, server_default="0"),
    Column("notes", Text),
    Column("is_active", Boolean, nullable=False, server_default="1"),
    Column("company_id", Integer, nullable=False, index=True),
)
Index("ix_customers_company_active_name", customers.c.company_id, customers.c.is_active, customers.c.name)

suppliers = Table(
    "suppliers",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(200), nullable=False),
    Column("owner_name", String(160)),
    Column("phone", String(60)),
    Column("email", String(200)),
    Column("address", Text),
    Column("tax_number", String(60)),
    Column("opening_balance", MONEY, nullable=False, server_default="0"),
    Column("risk_limit", MONEY, nullable=False, server_default="0"),
    Column("payment_term_days", Integer, nullable=False, server_default="0"),
    Column("notes", Text),
    Column("is_active", Boolean, nullable=False, server_default="1"),
    Column("company_id", Integer, nullable=False, index=True),
)
Index("ix_suppliers_company_active_name", suppliers.c.company_id, suppliers.c.is_active, suppliers.c.name)

products = Table(
    "products",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(240), nullable=False),
    Column("purchase_price", MONEY, nullable=False, server_default="0"),
    Column("sale_price", MONEY, nullable=False, server_default="0"),
    Column("category", String(160)),
    Column("country", String(100)),
    Column("vat_rate", MONEY, nullable=False, server_default="20"),
    Column("product_code", String(120)),
    Column("barcode", String(120)),
    Column("stock", QUANTITY, nullable=False, server_default="0"),
    Column("location", String(240)),
    Column("oem_number", String(160)),
    Column("alternative_oem", Text),
    Column("brand", String(160)),
    Column("manufacturer", String(160)),
    Column("compatible_models", Text),
    Column("technical_notes", Text),
    Column("supplier_id", Integer),
    Column("unit", String(40), nullable=False, server_default="Adet"),
    Column("units_per_box", QUANTITY),
    Column("price_per", String(40), nullable=False, server_default="Adet"),
    Column("active", Boolean, nullable=False, server_default="1"),
    Column("critical_stock", QUANTITY, nullable=False, server_default="0"),
    Column("minimum_stock", QUANTITY, nullable=False, server_default="0"),
    # NOTE: the purchase-engine reorder policy columns (reorder_point,
    # target_stock, migration 20260726_0024) are deliberately NOT declared here.
    # Like every other post-0015 purchasing column they live in Alembic only and
    # are read through raw ``text()`` SQL. Declaring them would enrol them in the
    # numeric manifest, whose contract is "present in the 0000 baseline" — a
    # later-added column shows up as a presence diff and breaks the numeric
    # migration reconciliation gate.
    Column("company_id", Integer, nullable=False, index=True),
    # Çocuğun bileşik yabancı anahtarının hedefi — `orders`taki ile aynı
    # gerekçe. Bugünkü tek çocuk `crop_seasons.product_id`
    # (`fk_crop_seasons_product_same_company`, göç 20260827_0062): bir
    # kiracının sezonu BAŞKA kiracının ürününü işaret edemesin diye referans
    # `company_id`yi de adlandırıyor, ve bunun için hedefin çift üzerinden
    # tekil olması gerekiyor. Taze kurulumda buradan, mevcut veritabanlarında
    # 20260827_0062'den gelir; iki yol AYNI kısıtı bırakmak zorunda.
    UniqueConstraint("company_id", "id", name="uq_products_company_id"),
)
Index("ix_products_company_active_name", products.c.company_id, products.c.active, products.c.name)
Index("ix_products_company_code", products.c.company_id, products.c.product_code)
Index("ix_products_company_barcode", products.c.company_id, products.c.barcode)
Index("ix_products_company_oem", products.c.company_id, products.c.oem_number)

orders = Table(
    "orders",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("customer_id", Integer, nullable=False, index=True),
    Column("order_date", String(30), nullable=False),
    Column("subtotal", MONEY, nullable=False, server_default="0"),
    Column("vat_total", MONEY, nullable=False, server_default="0"),
    Column("grand_total", MONEY, nullable=False, server_default="0"),
    Column("discount_percent", MONEY, nullable=False, server_default="0"),
    Column("discount_amount", MONEY, nullable=False, server_default="0"),
    Column("final_total", MONEY, nullable=False, server_default="0"),
    Column("document_no", String(120)),
    Column("due_date", String(30)),
    Column("due_date_normalized", Date),
    Column("note", Text),
    Column("company_id", Integer, nullable=False, index=True),
    Column("branch_id", Integer),
    Column("warehouse_id", Integer),
    Column("status", String(30), nullable=False, server_default="completed"),
    Column("payment_method", String(30), nullable=False, server_default="credit"),
    Column("paid_amount", MONEY, nullable=False, server_default="0"),
    # Harman vadesi (harvest-term) sales: PESIN keeps the legacy immediate-payment
    # behaviour; HARMAN_VADELI defers collection to the harvest season stored in
    # due_date. Defaulted so existing rows and PESIN sales are unaffected.
    Column("payment_term", String(20), nullable=False, server_default="PESIN"),
    # Çocuğun bileşik yabancı anahtarının hedefi. ``id`` tek başına zaten
    # tekil; çift, çocuğun aynı referansta company_id'yi de adlandırabilmesi
    # için var.
    UniqueConstraint("company_id", "id", name="uq_orders_company_id"),
)
Index("ix_orders_company_date", orders.c.company_id, orders.c.order_date)
Index("ix_orders_company_customer", orders.c.company_id, orders.c.customer_id)
Index("ix_orders_company_term_due", orders.c.company_id, orders.c.payment_term, orders.c.due_date)

order_items = Table(
    "order_items",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # Satırın kendi kiracısı. Ebeveynden türetilebilir olması yetmez: türetme
    # her sorgunun ebeveyni JOIN'leyip filtrelemesine bağlıdır ve bu bir
    # konvansiyondur, garanti değil.
    Column("company_id", Integer, nullable=False),
    Column("order_id", Integer, nullable=False, index=True),
    Column("product_id", Integer),
    Column("product_name", String(240), nullable=False),
    Column("quantity", QUANTITY, nullable=False),
    Column("unit_price", MONEY, nullable=False),
    Column("vat_rate", MONEY, nullable=False, server_default="20"),
    Column("line_subtotal", MONEY, nullable=False, server_default="0"),
    Column("line_vat", MONEY, nullable=False, server_default="0"),
    Column("line_total", MONEY, nullable=False),
    Column("discount_percent", MONEY, nullable=False, server_default="0"),
    Column("discount_amount", MONEY, nullable=False, server_default="0"),
    # Garantinin kendisi: çift ebeveynde VAR olmak zorunda, yani satır başka
    # bir firmanın belgesine bağlanamaz. Veritabanı zorluyor (db.py'de
    # PRAGMA foreign_keys=ON açık olduğu için SQLite'ta da).
    ForeignKeyConstraint(
        ["company_id", "order_id"],
        ["orders.company_id", "orders.id"],
        name="fk_order_items_order_same_company",
    ),
    Index("ix_order_items_company_order", "company_id", "order_id"),
)

purchases = Table(
    "purchases",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("supplier_id", Integer, nullable=False, index=True),
    Column("purchase_date", String(30), nullable=False),
    Column("subtotal", MONEY, nullable=False, server_default="0"),
    Column("vat_total", MONEY, nullable=False, server_default="0"),
    Column("grand_total", MONEY, nullable=False, server_default="0"),
    Column("discount_percent", MONEY, nullable=False, server_default="0"),
    Column("discount_amount", MONEY, nullable=False, server_default="0"),
    Column("final_total", MONEY, nullable=False, server_default="0"),
    Column("document_no", String(120)),
    Column("due_date", String(30)),
    Column("note", Text),
    Column("company_id", Integer, nullable=False, index=True),
    Column("branch_id", Integer),
    Column("warehouse_id", Integer),
    Column("status", String(30), nullable=False, server_default="completed"),
    Column("payment_method", String(30), nullable=False, server_default="credit"),
    Column("paid_amount", MONEY, nullable=False, server_default="0"),
    # Çocuğun bileşik yabancı anahtarının hedefi. ``id`` tek başına zaten
    # tekil; çift, çocuğun aynı referansta company_id'yi de adlandırabilmesi
    # için var.
    UniqueConstraint("company_id", "id", name="uq_purchases_company_id"),
)
Index("ix_purchases_company_date", purchases.c.company_id, purchases.c.purchase_date)
Index("ix_purchases_company_supplier", purchases.c.company_id, purchases.c.supplier_id)

purchase_items = Table(
    "purchase_items",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # Satırın kendi kiracısı. Ebeveynden türetilebilir olması yetmez: türetme
    # her sorgunun ebeveyni JOIN'leyip filtrelemesine bağlıdır ve bu bir
    # konvansiyondur, garanti değil.
    Column("company_id", Integer, nullable=False),
    Column("purchase_id", Integer, nullable=False, index=True),
    Column("product_id", Integer),
    Column("product_name", String(240), nullable=False),
    Column("quantity", QUANTITY, nullable=False),
    Column("unit_price", MONEY, nullable=False),
    Column("vat_rate", MONEY, nullable=False, server_default="20"),
    Column("line_subtotal", MONEY, nullable=False, server_default="0"),
    Column("line_vat", MONEY, nullable=False, server_default="0"),
    Column("line_total", MONEY, nullable=False),
    Column("discount_percent", MONEY, nullable=False, server_default="0"),
    Column("discount_amount", MONEY, nullable=False, server_default="0"),
    # PARTİ + SKT, 1B-A (göç 20260908_0073). Kalem partiyi AÇAN girdidir;
    # `product_lots` satırı ondan doğar ve kalemde saklanması, belgenin ne
    # söylediğinin BELGEDE kalması içindir — defter sonradan düzeltilse bile
    # alış fişi ne yazdığını söyleyebilmelidir.
    #
    # BURADA BİLDİRİLMESİ ÖLÇÜLDÜ, VARSAYILMADI. 0067 `core_schema`ya
    # DOKUNMAMIŞTI ve gerekçesi şuydu: bildirim SAYISAL MANİFESTODA bir
    # VARLIK FARKI üretir ve sayısal göç mutabakat kapısını kırar. O gerekçe
    # bir NUMERIC sütun (`quantity`) içindi. Bu ikisi NE `MONEY` NE `QUANTITY`
    # ailesindendir, yani `app/numeric_manifest.py`nin iki sözlüğünden
    # HİÇBİRİNE girmezler ve `capture_numeric_snapshot` onları GÖRMEZ.
    #
    # İki yol AYNI şemada buluşuyor: `20260712_0000` bu metadata'yı
    # `create_all` ile kurar (TAZE veritabanı sütunları ORADA alır) ve 0073
    # sütun VARLIĞINI SORARAK ekler, yani mevcut veritabanı onları GÖÇTE alır.
    Column("lot_code", String(80)),
    Column("expiry_date", Date),
    # Garantinin kendisi: çift ebeveynde VAR olmak zorunda, yani satır başka
    # bir firmanın belgesine bağlanamaz. Veritabanı zorluyor (db.py'de
    # PRAGMA foreign_keys=ON açık olduğu için SQLite'ta da).
    ForeignKeyConstraint(
        ["company_id", "purchase_id"],
        ["purchases.company_id", "purchases.id"],
        name="fk_purchase_items_purchase_same_company",
    ),
    Index("ix_purchase_items_company_purchase", "company_id", "purchase_id"),
)

payments = Table(
    "payments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("entity_type", String(20), nullable=False),
    Column("entity_id", Integer, nullable=False),
    Column("amount", MONEY, nullable=False),
    Column("payment_date", String(30), nullable=False),
    Column("note", Text),
    Column("company_id", Integer, nullable=False, index=True),
    Column("branch_id", Integer),
    Column("payment_method", String(30), nullable=False, server_default="cash"),
    Column("reference_type", String(60)),
    Column("reference_id", Integer),
    Column("account_id", Integer),
    Column("financial_transaction_id", Integer),
)
Index("ix_payments_company_entity", payments.c.company_id, payments.c.entity_type, payments.c.entity_id)
Index("ix_payments_company_date", payments.c.company_id, payments.c.payment_date)

stock_movements = Table(
    "stock_movements",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("product_id", Integer, nullable=False, index=True),
    Column("movement_type", String(40), nullable=False),
    Column("quantity", QUANTITY, nullable=False),
    Column("movement_date", String(30), nullable=False),
    Column("reference_type", String(60)),
    Column("reference_id", Integer),
    Column("note", Text),
    Column("company_id", Integer, nullable=False, index=True),
    Column("warehouse_id", Integer),
    # PARTİ DEFTERİ, 1B-C. Hareketin hangi partiden çıktığı/hangisine
    # girdiğinin kaydı; göç `20260903_0067` sütunu ZATEN açtı. Burada
    # BİLDİRİLMESİNİN tek sebebi ŞUDUR: `app/routers/warehouse_counts.py`
    # hareketi Core `insert(stock_movements)` ile yazar ve Core, metadata'da
    # BİLDİRİLMEMİŞ bir sütuna değer YAZAMAZ (`CompileError`). Ham `text()`
    # yazan yollar (products.py, transactions.py) bildirimsiz de yazabilirdi;
    # sayım yolu yazamaz.
    #
    # 0067 BURAYA DOKUNMAMIŞTI ve gerekçesi SAYISAL GÖÇ MUTABAKAT KAPISIYDI:
    # bildirim, sayısal manifestoda bir VARLIK FARKI üretir ve kapıyı kırar.
    # O gerekçe bir NUMERIC sütun içindi ve BU SÜTUN İÇİN ÖLÇÜLDÜ, VARSAYILMADI
    # (bkz. `tests/test_1b_c_ayarlama_lot.py`): `lot_id` `Integer`dır, NE
    # `MONEY` NE `QUANTITY` ailesindendir, `app/numeric_manifest.py`nin iki
    # sözlüğünden HİÇBİRİNE girmez ve `capture_numeric_snapshot` onu GÖRMEZ —
    # manifesto elle yazılan bir sütun ADI listesidir, `metadata`dan
    # TÜRETİLMEZ, yani buraya sütun eklemek onu KIMILDATAMAZ.
    #
    # ÖLÇÜLEN ASIL BEDEL BAŞKAYDI ve gerçekti: 0067'nin sütunu `if "lot_id"
    # not in ...` koşuluyla ekliyordu ve BİLEŞİK YABANCI ANAHTARI da AYNI
    # koşulun İÇİNDE kuruyordu. Bu bildirimle taze veritabanında sütunu
    # `20260712_0000`ın `create_all`ı açar, koşul YANLIŞ olur ve FK HİÇ
    # KURULMAZDI — sessizce. 0067'nin koşulu bu yüzden İKİYE AYRILDI (sütun
    # ayrı, kısıt ayrı) ve taze veritabanında FK'nin durduğu ADIYLA çivili.
    Column("lot_id", Integer),
)
Index("ix_stock_movements_company_product", stock_movements.c.company_id, stock_movements.c.product_id, stock_movements.c.id)

quotes = Table(
    "quotes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("customer_id", Integer, nullable=False, index=True),
    Column("quote_date", String(30), nullable=False),
    Column("valid_until", String(30)),
    Column("status", String(30), nullable=False, server_default="draft"),
    Column("note", Text),
    Column("total", MONEY, nullable=False, server_default="0"),
    Column("company_id", Integer, nullable=False, index=True),
    Column("document_no", String(120)),
    Column("warehouse_id", Integer),
    Column("subtotal", MONEY, nullable=False, server_default="0"),
    Column("vat_total", MONEY, nullable=False, server_default="0"),
    Column("discount_percent", MONEY, nullable=False, server_default="0"),
    Column("discount_amount", MONEY, nullable=False, server_default="0"),
    Column("grand_total", MONEY, nullable=False, server_default="0"),
    Column("converted_type", String(60)),
    Column("converted_id", Integer),
    # Çocuğun bileşik yabancı anahtarının hedefi. ``id`` tek başına zaten
    # tekil; çift, çocuğun aynı referansta company_id'yi de adlandırabilmesi
    # için var.
    UniqueConstraint("company_id", "id", name="uq_quotes_company_id"),
)
Index("ix_quotes_company_date", quotes.c.company_id, quotes.c.quote_date)

quote_items = Table(
    "quote_items",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # Satırın kendi kiracısı. Ebeveynden türetilebilir olması yetmez: türetme
    # her sorgunun ebeveyni JOIN'leyip filtrelemesine bağlıdır ve bu bir
    # konvansiyondur, garanti değil.
    Column("company_id", Integer, nullable=False),
    Column("quote_id", Integer, nullable=False, index=True),
    Column("product_id", Integer),
    Column("product_name", String(240), nullable=False),
    Column("quantity", QUANTITY, nullable=False),
    Column("unit_price", MONEY, nullable=False),
    Column("vat_rate", MONEY, nullable=False, server_default="20"),
    Column("line_total", MONEY, nullable=False),
    Column("discount_percent", MONEY, nullable=False, server_default="0"),
    Column("discount_amount", MONEY, nullable=False, server_default="0"),
    Column("line_subtotal", MONEY, nullable=False, server_default="0"),
    Column("line_vat", MONEY, nullable=False, server_default="0"),
    # Garantinin kendisi: çift ebeveynde VAR olmak zorunda, yani satır başka
    # bir firmanın belgesine bağlanamaz. Veritabanı zorluyor (db.py'de
    # PRAGMA foreign_keys=ON açık olduğu için SQLite'ta da).
    ForeignKeyConstraint(
        ["company_id", "quote_id"],
        ["quotes.company_id", "quotes.id"],
        name="fk_quote_items_quote_same_company",
    ),
    Index("ix_quote_items_company_quote", "company_id", "quote_id"),
)

returns = Table(
    "returns",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("return_type", String(30), nullable=False),
    Column("entity_id", Integer, nullable=False),
    Column("return_date", String(30), nullable=False),
    Column("document_no", String(120)),
    Column("note", Text),
    Column("total", MONEY, nullable=False, server_default="0"),
    Column("company_id", Integer, nullable=False, index=True),
    Column("warehouse_id", Integer),
    Column("status", String(30), nullable=False, server_default="completed"),
    Column("source_type", String(60)),
    Column("source_id", Integer),
    Column("subtotal", MONEY, nullable=False, server_default="0"),
    Column("vat_total", MONEY, nullable=False, server_default="0"),
    # Çocuğun bileşik yabancı anahtarının hedefi. ``id`` tek başına zaten
    # tekil; çift, çocuğun aynı referansta company_id'yi de adlandırabilmesi
    # için var.
    UniqueConstraint("company_id", "id", name="uq_returns_company_id"),
)
Index("ix_returns_company_date", returns.c.company_id, returns.c.return_date)

return_items = Table(
    "return_items",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # Satırın kendi kiracısı. Ebeveynden türetilebilir olması yetmez: türetme
    # her sorgunun ebeveyni JOIN'leyip filtrelemesine bağlıdır ve bu bir
    # konvansiyondur, garanti değil.
    Column("company_id", Integer, nullable=False),
    Column("return_id", Integer, nullable=False, index=True),
    Column("product_id", Integer),
    Column("product_name", String(240), nullable=False),
    Column("quantity", QUANTITY, nullable=False),
    Column("unit_price", MONEY, nullable=False),
    Column("line_total", MONEY, nullable=False),
    Column("vat_rate", MONEY, nullable=False, server_default="20"),
    Column("line_subtotal", MONEY, nullable=False, server_default="0"),
    Column("line_vat", MONEY, nullable=False, server_default="0"),
    # Garantinin kendisi: çift ebeveynde VAR olmak zorunda, yani satır başka
    # bir firmanın belgesine bağlanamaz. Veritabanı zorluyor (db.py'de
    # PRAGMA foreign_keys=ON açık olduğu için SQLite'ta da).
    ForeignKeyConstraint(
        ["company_id", "return_id"],
        ["returns.company_id", "returns.id"],
        name="fk_return_items_return_same_company",
    ),
    Index("ix_return_items_company_return", "company_id", "return_id"),
)

income_expenses = Table(
    "income_expenses",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("txn_date", String(30), nullable=False),
    Column("txn_type", String(30), nullable=False),
    Column("category", String(120)),
    Column("amount", MONEY, nullable=False),
    Column("account_id", Integer),
    Column("note", Text),
    Column("company_id", Integer, nullable=False, index=True),
)

# Platform-global markers only. Never store company-specific data here; company
# settings belong on ``companies`` or in a table that carries ``company_id``.
settings_table = Table(
    "settings",
    metadata,
    Column("key", String(160), primary_key=True),
    Column("value", Text),
)

# Per-company, per-document-type counters.  A composite primary key makes the
# counter row a natural concurrency lock on PostgreSQL and SQLite.
#
# Keeping document numbering independent from business table primary keys avoids
# reusing numbers after deletion and prevents two concurrent requests from
# receiving the same generated document number.
document_sequences = Table(
    "document_sequences",
    metadata,
    Column("company_id", Integer, primary_key=True),
    Column("sequence_key", String(160), primary_key=True),
    Column("current_value", Integer, nullable=False, server_default="0"),
)


def _add_column(conn: Connection, table_name: str, column: Column) -> None:
    columns = {item["name"] for item in inspect(conn).get_columns(table_name)}
    if column.name in columns:
        return
    preparer = conn.dialect.identifier_preparer
    table_sql = preparer.quote(table_name)
    column_sql = str(CreateColumn(column).compile(dialect=conn.dialect))
    conn.execute(text(f"ALTER TABLE {table_sql} ADD COLUMN {column_sql}"))


def _make_company_id_required(conn: Connection, table_name: str) -> None:
    company_column = next(
        item for item in inspect(conn).get_columns(table_name)
        if item["name"] == "company_id"
    )
    if not company_column["nullable"] and company_column["default"] is None:
        return

    operations = Operations(MigrationContext.configure(conn))
    with operations.batch_alter_table(table_name) as batch_op:
        batch_op.alter_column(
            "company_id",
            existing_type=Integer(),
            existing_nullable=bool(company_column["nullable"]),
            nullable=False,
            server_default=None,
        )


def _backfill_company_id_from_existing_company(
    conn: Connection,
    table_name: str,
) -> None:
    preparer = conn.dialect.identifier_preparer
    table_sql = preparer.quote(table_name)
    missing_tenant = conn.execute(
        text(f"SELECT 1 FROM {table_sql} WHERE company_id IS NULL LIMIT 1")
    ).first()
    if missing_tenant is None:
        return

    if not inspect(conn).has_table("companies"):
        raise RuntimeError(
            f"{table_name} icin company_id backfill yapilamadi: companies tablosu yok"
        )
    company_id = conn.execute(
        text("SELECT id FROM companies ORDER BY id LIMIT 1")
    ).scalar_one_or_none()
    if company_id is None:
        raise RuntimeError(
            f"{table_name} icin company_id backfill yapilamadi: sirket yok"
        )

    conn.execute(
        text(f"UPDATE {table_sql} SET company_id=:company_id WHERE company_id IS NULL"),
        {"company_id": company_id},
    )


def initialize_core_schema(engine: Engine) -> None:
    """Create the portable core ERP schema and safely extend legacy installations."""
    metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        inspector = inspect(conn)
        if inspector.has_table("income_expenses"):
            _add_column(conn, "income_expenses", Column("company_id", Integer))
            _backfill_company_id_from_existing_company(conn, "income_expenses")
            _make_company_id_required(conn, "income_expenses")
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_income_expenses_company ON income_expenses(company_id)"))
