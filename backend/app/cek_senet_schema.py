"""`cek_senetler` defterinin Core tanımı (CS1, göç ``20260914_0085``).

TABLONUN TEK DOĞUM YERİ GÖÇTÜR. Bu MetaData uygulama açılışında
``create_all`` EDİLMEZ — ``app/whatsapp/schema.py`` ile aynı desen. Açılış
DDL'i tabloyu göçten ÖNCE kursaydı göç onu VAR bulup atlar ve CHECK/bileşik
FK'ler HİÇ kurulmazdı (0072'de ölçülen kusur; kapı:
``tests/test_cs1_cek_senet.py::test_ACILIS_DDLi_GOCUN_ONUNE_GECMIYOR``).

Buradaki tanım uç katmanının SORGU yüzeyidir: sütun TİPLERİ (Date,
Numeric(18,2)) iki lehçede de doğru bağlama/okuma için gerekir. Kısıtlar
göçte durur ve buraya KOPYALANMAZ; kopya, göçten ayrışabilecek ikinci bir
gerçek kaynağı olurdu. Tek istisna tiplerdir ve onların eşitliği test
edilir (``test_SEMA_MODULU_goc_ile_AYNI_SUTUNLAR``).

Python adı tablo adıyla AYNIDIR: kiracı kapsam kapısı ve Core sorgu envanteri
tabloyu ADIYLA tanır; farklı bir ad iki kapıya da görünmez olurdu.
"""
from __future__ import annotations

from sqlalchemy import Column, Date, DateTime, Integer, MetaData, Numeric, String, Table, Text

metadata = MetaData()

cek_senetler = Table(
    "cek_senetler",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, nullable=False),
    Column("tur", String(20), nullable=False),
    Column("yon", String(20), nullable=False),
    Column("portfoy_durumu", String(30), nullable=False),
    Column("customer_id", Integer),
    Column("supplier_id", Integer),
    Column("endorsed_supplier_id", Integer),
    Column("endorsed_date", Date),
    Column("tutar", Numeric(18, 2), nullable=False),
    Column("vade", Date, nullable=False),
    Column("keside_tarihi", Date),
    Column("banka_adi", String(160)),
    Column("sube_adi", String(120)),
    Column("hesap_no", String(100)),
    Column("seri_no", String(100), nullable=False),
    Column("kesideci", String(200)),
    Column("tahsil_hesap_id", Integer),
    Column("tahsil_tarihi", Date),
    Column("payment_id", Integer),
    Column("financial_transaction_id", Integer),
    Column("charge_document_id", Integer),
    Column("notlar", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by", Integer),
)
