"""e-İrsaliye kısmi sevkin Core sorgu yüzeyi (E4b-1, göç ``20260915_0087``).

`app/cek_senet_schema.py` ile AYNI desen: bu MetaData uygulama açılışında
``create_all`` EDİLMEZ. Tabloların tek doğum yeri göçtür (0083 ve 0087);
açılış DDL'i onları göçten önce kursaydı göç VAR bulup atlar ve bileşik
FK / CHECK hiç kurulmazdı (0072'de ölçülen kusur).

Buradaki tanımlar yalnız SORGU yüzeyidir ve yalnız okunan/yazılan sütunları
taşır; kısıtlar göçte durur. `invoices` ve `invoice_items` burada KISMİ:
kısmi sevk o iki tablodan yalnız aşağıdaki sütunları okur (faturanın satır
kilidi `invoices.updated_at`e, kalan miktar `invoice_items.quantity`e
dayanır).

Python adları tablo adlarıyla AYNIDIR: kiracı kapsam kapısı ve Core sorgu
envanteri tabloyu ADIYLA tanır.
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, MetaData, Numeric, String, Table, Text

metadata = MetaData()

despatch_lines = Table(
    "despatch_lines",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, nullable=False),
    Column("despatch_id", Integer, nullable=False),
    Column("invoice_item_id", Integer, nullable=False),
    Column("line_no", Integer, nullable=False),
    Column("product_id", Integer),
    Column("item_name", Text, nullable=False),
    Column("quantity", Numeric(18, 4), nullable=False),
    Column("unit_code", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

despatch_notes = Table(
    "despatch_notes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, nullable=False),
    Column("invoice_id", Integer, nullable=False),
    Column("despatch_number", String(40)),
)

invoice_items = Table(
    "invoice_items",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, nullable=False),
    Column("invoice_id", Integer, nullable=False),
    Column("item_type", String(20), nullable=False),
    Column("description", String(500), nullable=False),
    Column("quantity", Numeric(18, 4), nullable=False),
    Column("source_snapshot", Text, nullable=False),
)

invoices = Table(
    "invoices",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
