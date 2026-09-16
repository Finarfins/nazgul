"""e-İrsaliye kısmi sevk ve ticari yanıtın Core sorgu yüzeyi (E4b-1 göç
``20260915_0087``, E4b-2 göç ``20260915_0089``).

`app/cek_senet_schema.py` ile AYNI desen: bu MetaData uygulama açılışında
``create_all`` EDİLMEZ. Tabloların tek doğum yeri göçtür (0083, 0087, 0089);
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

from sqlalchemy import Column, Date, DateTime, Integer, MetaData, Numeric, String, Table, Text

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
    # E4b-2 (göç 0089): sync'in satır kilidi ve yanıt özeti.
    Column("edespatch_status", String(20), nullable=False),
    Column("response_status", String(20)),
    Column("response_received_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

#: E4b-2 (göç `20260915_0089`): alıcının ticari yanıt belgesi (ReceiptAdvice).
despatch_responses = Table(
    "despatch_responses",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, nullable=False),
    Column("despatch_id", Integer, nullable=False),
    Column("response_uuid", String(36), nullable=False),
    Column("response_number", String(16), nullable=False),
    Column("response_type", String(20), nullable=False),
    Column("issue_date", Date, nullable=False),
    Column("notes", Text),
    Column("raw_xml", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

despatch_response_lines = Table(
    "despatch_response_lines",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, nullable=False),
    Column("response_id", Integer, nullable=False),
    Column("despatch_line_id", Integer, nullable=False),
    Column("received_quantity", Numeric(18, 4), nullable=False),
    Column("rejected_quantity", Numeric(18, 4), nullable=False),
    Column("reject_reason", String(200)),
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
