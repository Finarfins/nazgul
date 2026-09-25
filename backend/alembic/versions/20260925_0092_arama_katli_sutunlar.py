"""KALICI KATLANMIS ARAMA SUTUNLARI (H75).

Revision ID: 20260925_0092
Revises: 20260920_0091

--- OLCULEN SORUN -------------------------------------------------------

H57 (#148) arama kolonlarini ISTEK ANINDA `translate(kolon,KAYNAK,HEDEF)`
ile katliyordu. `LIKE '%x%'` zaten sirali taramadir; ama katlama her
satirda, her kolonda yeniden hesaplandigi icin secicilikten BAGIMSIZ bir
taban maliyet doguyordu. 20k cari, `musteri_satirlari`, medyan (bu PR'in
tezgahi, #148 tur-3 merceginin olcumuyle ayni yon):

    q=ltd      PG 15.9 -> 214 ms    SQLite 26.1 -> 96.9 ms
    q=Ltd 123  PG 13.8 -> 276 ms    SQLite 17.5 -> 89.8 ms

--- NE YAPAR ------------------------------------------------------------

`_SUTUNLAR`daki her `(tablo, kolon)` icin NULL olabilen bir `<kolon>_katli`
TEXT sutunu ekler ve MEVCUT satirlari `app.arama.katli_sql` ile, yani
uygulamanin yazicisi `app/arama_katli.katli_esitle`nin kullandigi AYNI SQL
metniyle doldurur: `translate(COALESCE(kolon,''),KAYNAK,HEDEF)`. PG'de
`translate` yerlesiktir; SQLite'ta bu goc islevi baglantiya KENDISI
kaydeder, cunku uretimdeki tek-kullanimlik `python -m alembic upgrade head`
`app.db`yi ICE AKTARMAZ ve oradaki kanca hic kurulmaz (olculdu:
`alembic/env.py` yalniz `app.config`i okur).

Sutunlar `core_schema`ya YAZILMAZ: 0015 sonrasi her sutun gibi yalniz
Alembic'te yasar (bkz. `core_schema.products` notu) ve ham SQL ile okunur.

--- BAKIM: TETIKLEYICI YOK, URETILMIS SUTUN YOK ------------------------

Deger UYGULAMA tarafindan tutulur (`app/arama_katli.py`). Tetikleyici
SQLite/PG esdegerligini ve kiraci geri yukleme anlamini bozardi; PG
uretilmis sutunu SQLite'ta kurulamaz (ifade bir kullanici islevine
dayaniyor). Kacan bir yazici `tests/test_h75_katli_sutun_esitligi.py`
tarafindan olculur.

--- GERI ALMA -----------------------------------------------------------

Simetrik. Dusen sey TURETILMIS veridir: kaynak kolonlar dokunulmaz ve
geri alinmis surumun arama SQL'i katlamayi yine istek aninda yapar.
"""

from __future__ import annotations

import sqlite3

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

from app.arama import katli_sql, sqlite_katlamayi_kaydet

revision = "20260925_0092"
down_revision = "20260920_0091"
branch_labels = None
depends_on = None

#: GOC ANINDAKI kapali liste. `app.arama_katli.KATLI_SUTUNLAR` ile ayni
#: oldugunu `tests/test_h75_katli_sutun_esitligi.py` olcer.
_SUTUNLAR: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("customers", ("name", "owner_name", "email")),
    ("suppliers", ("name", "owner_name", "email")),
    ("products", ("name", "product_code", "barcode")),
    ("orders", ("document_no",)),
    ("purchases", ("document_no",)),
    ("finance_transactions", ("description",)),
)


def _sqlite_islevini_kaydet(bind) -> None:
    ham = bind.connection.driver_connection
    if isinstance(ham, sqlite3.Connection):
        sqlite_katlamayi_kaydet(ham)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _sqlite_islevini_kaydet(bind)
    inspector = sa.inspect(bind)
    for tablo, kolonlar in _SUTUNLAR:
        mevcut = {c["name"] for c in inspector.get_columns(tablo)}
        for kolon in kolonlar:
            if f"{kolon}_katli" not in mevcut:
                op.add_column(tablo, sa.Column(f"{kolon}_katli", sa.Text(), nullable=True))
        atama = ",".join(
            f"{kolon}_katli={katli_sql(f'COALESCE({kolon},{chr(39) * 2})')}" for kolon in kolonlar
        )
        bind.execute(text(f"UPDATE {tablo} SET {atama}"))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for tablo, kolonlar in reversed(_SUTUNLAR):
        mevcut = {c["name"] for c in inspector.get_columns(tablo)}
        for kolon in reversed(kolonlar):
            if f"{kolon}_katli" in mevcut:
                op.drop_column(tablo, f"{kolon}_katli")
