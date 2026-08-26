"""1. dilim: dört satır tablosunun ŞEMA kapısı (20260812_0058).

Dilim 2 (#56) ile aynı desen, dört çifte uygulanmış. Kapı iki şeyi ölçüyor:

1. **Yeniden kurulum bir şey DÜŞÜRMESİN.** SQLite'ta ebeveyne
   ``UNIQUE(company_id, id)`` eklemek tabloyu YENİDEN KURAR. Bu dilimde en
   büyük yeniden kurulum yüzeyi ``orders``: yedi indeks ve bir yabancı
   anahtar. Bunlardan biri sessizce kaybolsa hiçbir davranış testi bunu
   göremez — yalnız yavaşlar.
2. **Beyan edilmemiş EKLEME de hatadır.** Karşılaştırma tam eşitlik,
   alt küme değil (bkz. ``tenant_schema_snapshot.capa_dogrula``).

BAĞIMSIZ ÇAPA: ``TABAN_INDEKS`` bu migration VAR OLMADAN ölçüldü — ayrı bir
git worktree'de, zincir başı ``20260812_0057`` olan bir ağaçta ``inspect()``
ile sayılarak. Yani test edilen kod tarafından üretilemez. #56'nın dersi
buydu: türetilmiş "değişiklik öncesi" şema bağımsız bir parmak izi değildir,
çünkü migration yukarı çıkarken bozarsa hasar iki fotoğrafa da işlenir.
"""
from __future__ import annotations

import importlib.util as _iu
import os
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

BACKEND = Path(__file__).resolve().parents[1]

_spec = _iu.spec_from_file_location("snap", BACKEND / "tests" / "tenant_schema_snapshot.py")
snap = _iu.module_from_spec(_spec)
_spec.loader.exec_module(snap)

#: (çocuk, ebeveyn, FK sütunu, UNIQUE adı, FK adı, indeks adı)
CIFTLER = (
    ("order_items", "orders", "order_id",
     "uq_orders_company_id", "fk_order_items_order_same_company",
     "ix_order_items_company_order"),
    ("purchase_items", "purchases", "purchase_id",
     "uq_purchases_company_id", "fk_purchase_items_purchase_same_company",
     "ix_purchase_items_company_purchase"),
    ("quote_items", "quotes", "quote_id",
     "uq_quotes_company_id", "fk_quote_items_quote_same_company",
     "ix_quote_items_company_quote"),
    ("return_items", "returns", "return_id",
     "uq_returns_company_id", "fk_return_items_return_same_company",
     "ix_return_items_company_return"),
)

#: DEĞİŞİKLİK ÖNCESİ TABAN — BAĞIMSIZ ÇAPA.
#: origin/develop (zincir başı 20260812_0057) üzerinde, 20260812_0058 hiç
#: yokken ``inspect()`` ile sayıldı. ``orders`` yedi indeks taşıyor; ikisi
#: birbirine çok benziyor (``due_date`` ve ``due_date_normalized``) ve bu
#: yüzden birinin kaybı OKUYARAK fark edilmesi en zor olandır.
TABAN_INDEKS: dict[str, set] = {
    "orders": {
        (("company_id",), False),
        (("company_id", "customer_id"), False),
        (("company_id", "harvest_calendar_id"), False),
        (("company_id", "order_date"), False),
        (("company_id", "payment_term", "due_date"), False),
        (("company_id", "payment_term", "due_date_normalized"), False),
        (("customer_id",), False),
    },
    "order_items": {(("order_id",), False)},
    "purchases": {
        (("company_id",), False),
        (("company_id", "purchase_date"), False),
        (("company_id", "supplier_id"), False),
        (("supplier_id",), False),
    },
    "purchase_items": {(("purchase_id",), False)},
    "quotes": {
        (("company_id",), False),
        (("company_id", "quote_date"), False),
        (("customer_id",), False),
    },
    "quote_items": {(("quote_id",), False)},
    "returns": {
        (("company_id",), False),
        (("company_id", "return_date"), False),
    },
    "return_items": {(("return_id",), False)},
}

GOC_YOLU = BACKEND / "alembic" / "versions" / "20260812_0058_line_items_tenant_slice1.py"


@pytest.fixture(scope="module")
def motor():
    """Zincirle kurulmuş veritabanı. DATABASE_URL varsa o kulvar kullanılır."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL yok; kulvar seçilmeden şema kapısı koşmaz")
    import app.main  # noqa: F401  — zinciri koşturur
    return create_engine(url)


@pytest.fixture(scope="module")
def gecis():
    spec = _iu.spec_from_file_location("gecis_0058", GOC_YOLU)
    modul = _iu.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _kostur(motor, gecis, asama: str) -> None:
    with motor.begin() as baglanti:
        onceki = gecis.op
        gecis.op = Operations(MigrationContext.configure(baglanti))
        try:
            getattr(gecis, asama)()
        finally:
            gecis.op = onceki


def test_yeniden_kurulum_hicbir_seyi_dusurmuyor(motor, gecis) -> None:
    """Ebeveyn yeniden kurulurken indeksleri ve FK'ları KORUNMALI."""
    pg = motor.dialect.name == "postgresql"

    # --- gerçekten türetilmiş "değişiklik öncesi" şekil --------------------
    _kostur(motor, gecis, "downgrade")
    denetci = inspect(motor)
    ONCE: dict[str, dict] = {}
    for cocuk, ebeveyn, _fk, _uq, _fkad, _ix in CIFTLER:
        for tablo in (cocuk, ebeveyn):
            ONCE[tablo] = snap.yapisal_iz(denetci, tablo)
            # BAĞIMSIZ ÇAPA: fixture kirliyse sonraki iddialar anlamsız.
            snap.capa_dogrula(denetci, tablo, TABAN_INDEKS[tablo])
        # FIXTURE DOĞRULAMASI (fail-closed)
        assert "company_id" not in ONCE[cocuk]["sutunlar"], (
            f"türetilen eski şekil {cocuk} için hâlâ company_id taşıyor")
        assert ("company_id", "id") not in ONCE[ebeveyn]["tekillik"], (
            f"türetilen eski şekil {ebeveyn} için hâlâ UNIQUE(company_id,id) taşıyor")

    # --- yükselt ve KARŞILAŞTIR -------------------------------------------
    _kostur(motor, gecis, "upgrade")
    denetci = inspect(motor)
    for cocuk, ebeveyn, fk_sutun, uq_ad, fk_ad, index_ad in CIFTLER:
        snap.korunmus_mu(
            ebeveyn, ONCE[ebeveyn], snap.yapisal_iz(denetci, ebeveyn), "yükseltme sonrası",
            eklenen_tekillik=frozenset({("company_id", "id")}),
            # PostgreSQL UNIQUE kısıtı için ayrıca indeks gösterir; SQLite göstermez.
            eklenen_indeks_sutunlari=frozenset({(("company_id", "id"), True)}) if pg else frozenset(),
            eklenen_indeks_adlari=frozenset({uq_ad}) if pg else frozenset(),
        )
        snap.korunmus_mu(
            cocuk, ONCE[cocuk], snap.yapisal_iz(denetci, cocuk), "yükseltme sonrası",
            eklenen_indeks_sutunlari=frozenset({(("company_id", fk_sutun), False)}),
            eklenen_indeks_adlari=frozenset({index_ad}),
            eklenen_yabanci_anahtar=frozenset(
                {(("company_id", fk_sutun), ebeveyn, ("company_id", "id"))}
            ),
            eklenen_sutunlar={"company_id": ("INTEGER", False)},
        )


def test_geri_alma_tabana_donuyor(motor, gecis) -> None:
    """downgrade, şekli bağımsız tabana geri getirmeli."""
    _kostur(motor, gecis, "downgrade")
    denetci = inspect(motor)
    for cocuk, ebeveyn, *_ in CIFTLER:
        for tablo in (cocuk, ebeveyn):
            snap.capa_dogrula(denetci, tablo, TABAN_INDEKS[tablo], asama="geri alma sonrası")
    _kostur(motor, gecis, "upgrade")
