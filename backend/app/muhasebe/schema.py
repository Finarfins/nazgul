"""Muhasebe Core tanımları (ORM'siz) — F9-5a.

İKİ ayrı şey burada durur ve ikisi de ayrı bir `MetaData`dadır; hiçbiri
uygulama açılışında `create_all` EDİLMEZ:

1. `muhasebe_hesap_eslemeleri` — göç `20260927_0093` ile BİREBİR (CHECK adları
   ve kapalı küme `OLAYLAR` İKİ yerde de aynı; 0079 geleneği). Tablonun TEK
   doğum yeri göçtür. Kapı: `tests/test_f9_5a_muhasebe_fisi.py::
   test_OLAYLAR_goc_ve_Core_tanimda_BIREBIR`.
2. `invoices` / `invoice_items` / `producer_receipts` / `producer_receipt_items`
   için DAR OKUMA tanımları — yalnız `kaynak.py`nin okuduğu sütunlar. Bu
   tablolar `core_schema`da yoktur (0012 ve 0070 onları yalnız göçte kurar);
   Core sorgusu yazabilmek için sütunların bildirilmesi gerekir ve bildirim
   ŞEMAYI DEĞİŞTİRMEZ (bu MetaData hiçbir yerde `create_all` edilmez).
   Python adı SQL tablo adıyla AYNI — Core kiracı kapısı tabloyu ADINDAN
   tanır (`tests/test_core_tenant_scoping_guard.py`, TABLO_TAKMA_ADLARI).
"""
from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()
okuma_metadata = MetaData()

TABLO = "muhasebe_hesap_eslemeleri"

#: Olay kapalı kümesi (keşif §4.2). Göçün `ck_mhe_olay` CHECK'i ile BİREBİR.
OLAYLAR: tuple[str, ...] = (
    "SATIS_CARI",
    "SATIS_GELIR",
    "SATIS_KDV",
    "SATIS_IADE",
    "ALIS_CARI",
    "ALIS_STOK",
    "ALIS_KDV",
    "ALIS_IADE",
    "SERVIS_GELIR",
    "MUSTAHSIL_STOPAJ",
    "MUSTAHSIL_BAGKUR",
    "KASA",
    "BANKA",
    "POS",
    "ALINAN_CEK",
    "VADE_FARKI_GELIR",
)

#: `taraf_tipi` kapalı kümesi; NULL = taraftan bağımsız.
TARAF_TIPLERI: tuple[str, ...] = ("CUSTOMER", "SUPPLIER")

#: İki kısmi tekil (göç başlığı). `kdv_orani` NULL/NOT NULL ikiye bölünür;
#: `taraf_tipi` da NULL olabildiği için ikisinde de `COALESCE(taraf_tipi,'')`
#: ifadesiyle anahtara girer — yoksa `(firma, olay, oran, NULL)` iki kez
#: yazılabilirdi (NULL'lar tekilde birbirinden FARKLI sayılır).
TEKIL_ORANLI = "uq_mhe_oranli"
TEKIL_ORANSIZ = "uq_mhe_oransiz"


def olay_check() -> str:
    return "olay IN (%s)" % ",".join("'" + o + "'" for o in OLAYLAR)


def taraf_check() -> str:
    return "taraf_tipi IS NULL OR taraf_tipi IN (%s)" % ",".join(
        "'" + t + "'" for t in TARAF_TIPLERI
    )


KDV_ORANI_CHECK = "kdv_orani IS NULL OR (kdv_orani >= 0 AND kdv_orani <= 100)"

# Ad LİTERAL: Core kiracı kapısı adı olmayan `Table(...)`ı yansıma sayar.
muhasebe_hesap_eslemeleri = Table(
    "muhasebe_hesap_eslemeleri",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # FK (`companies.id`) göçtedir; ayrı MetaData'da çözülemez
    # (`whatsapp/schema.py` ile AYNI gelenek).
    Column("company_id", Integer, nullable=False),
    Column("olay", String(40), nullable=False),
    Column("kdv_orani", Numeric(9, 4), nullable=True),
    Column("taraf_tipi", String(20), nullable=True),
    Column("hesap_kodu", String(40), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(olay_check(), name="ck_mhe_olay"),
    CheckConstraint(KDV_ORANI_CHECK, name="ck_mhe_kdv_orani"),
    CheckConstraint(taraf_check(), name="ck_mhe_taraf_tipi"),
    UniqueConstraint("company_id", "id", name="uq_muhasebe_hesap_eslemeleri_company_id"),
)

# --- DAR OKUMA TANIMLARI (şemayı değiştirmez; başlık madde 2) ---------------

invoices = Table(
    "invoices",
    okuma_metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, nullable=False),
    Column("invoice_number", String(80), nullable=False),
    Column("status", String(20), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("exchange_rate", Numeric(18, 6), nullable=False),
    Column("customer_snapshot", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

invoice_items = Table(
    "invoice_items",
    okuma_metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, nullable=False),
    Column("invoice_id", Integer, nullable=False),
    Column("tax_rate", Numeric(9, 4), nullable=False),
    Column("tax_amount", Numeric(18, 2), nullable=False),
    Column("total", Numeric(18, 2), nullable=False),
    Column("customer_payable", Numeric(18, 2), nullable=False),
    Column("company_payable", Numeric(18, 2), nullable=False),
)

producer_receipts = Table(
    "producer_receipts",
    okuma_metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, nullable=False),
    Column("supplier_id", Integer, nullable=False),
    Column("purchase_id", Integer, nullable=True),
    Column("receipt_no", String(60), nullable=True),
    Column("issued_at", DateTime(timezone=True), nullable=True),
    Column("status", String(20), nullable=False),
)

producer_receipt_items = Table(
    "producer_receipt_items",
    okuma_metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, nullable=False),
    Column("receipt_id", Integer, nullable=False),
    Column("line_gross", Numeric(18, 2), nullable=False),
    Column("withholding_amount", Numeric(18, 2), nullable=False),
    Column("social_security_amount", Numeric(18, 2), nullable=False),
    Column("line_net", Numeric(18, 2), nullable=False),
)
