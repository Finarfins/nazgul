"""MUHASEBE HESAP PLANI ESLEMESI (F9-5a).

Revision ID: 20260927_0093
Revises: 20260925_0092

--- NE YAPAR ------------------------------------------------------------

Tek yeni KIRACI tablosu `muhasebe_hesap_eslemeleri` (kesif §4.2): firmanin
bir muhasebe OLAYINI (satis cari, satis KDV'si, alis stok...) hangi Tek
Duzen hesabina yazdigi. `TENANT_TABLES` 127 -> 128.

TOHUM YAZILMAZ. Tek Duzen varsayilanlari KODDADIR
(`app/muhasebe/hesap_plani.py::VARSAYILANLAR`); bos tablo "hep varsayilan"
demektir. Tohum yazmak, varsayilani degistiren her surumde bir veri goc
gerektirirdi ve firmanin bilerek sectigi kod ile hic dokunmadigi varsayilan
AYIRT EDILEMEZDI.

--- SUTUNLAR ------------------------------------------------------------

* `olay`       — KAPALI kume, `ck_mhe_olay`; `app/muhasebe/schema.py::OLAYLAR`
                 ile BIREBIR (0079 gelenegi).
* `kdv_orani`  — NULL = orandan bagimsiz eslem; `ck_mhe_kdv_orani` 0..100.
* `taraf_tipi` — NULL = taraftan bagimsiz; `CUSTOMER|SUPPLIER`.
* `hesap_kodu` — `'120'`, `'391.20'`... Bicim (`^\\d{3}(\\.\\d{2})*$`) UCTA
                 dogrulanir; DB'de regex CHECK iki lehcede tasinabilir DEGIL.
* `updated_at` — timestamptz. `created_by` BILEREK YOK: denetim izi
                 `activity_logs`tadir (`accounting.account_map_updated`,
                 once/sonra). Bu sayede tabloda `company_id` disinda `*_id`
                 sutunu YOK ve kiraci geri yukleme siniflandiricisina satir
                 gerekmez (kesif §4.3).

--- TEKILLIK: IKI KISMI INDEKS ------------------------------------------

Istenen: `UNIQUE(company_id, olay, kdv_orani, taraf_tipi)` ve NULL'lar ESIT
sayilarak. Duz bir UNIQUE bunu YAPMAZ: SQL'de NULL'lar tekilde birbirinden
FARKLIDIR, yani `(firma, SATIS_CARI, NULL, NULL)` iki kez yazilabilirdi ve
`hesap_kodu` cozumu hangi satirin donecegine bagli kalirdi. PG15+ `NULLS NOT
DISTINCT` tasir ama SQLite TASIMAZ; iki lehcede AYNI calisan bicim:

* `uq_mhe_oranli`  (company_id, olay, kdv_orani, COALESCE(taraf_tipi,''))
                   WHERE kdv_orani IS NOT NULL
* `uq_mhe_oransiz` (company_id, olay, COALESCE(taraf_tipi,''))
                   WHERE kdv_orani IS NULL

`taraf_tipi` da NULL olabildigi icin IKISINDE de ifade olarak girer (kesif
§4.2 yalniz `kdv_orani`nin NULL'unu anmisti; `taraf_tipi` NULL'u ayni
delige dusuyordu). Davranis PG ikizinde olculuyor.

--- GERI ALMA -----------------------------------------------------------

Simetrik ve KOSULLU. Dusen sey firmanin hesap eslemesidir; geri alinmis
surum eslemeyi hic bilmiyor (dogrudan veri kaybi, bilincli).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260927_0093"
down_revision = "20260925_0092"
branch_labels = None
depends_on = None

TABLO = "muhasebe_hesap_eslemeleri"

#: Kapali kume. `app/muhasebe/schema.py::OLAYLAR` ile BIREBIR ayni.
OLAYLAR = (
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
TARAF_TIPLERI = ("CUSTOMER", "SUPPLIER")

FIRMA_KIMLIK = "uq_muhasebe_hesap_eslemeleri_company_id"
TEKIL_ORANLI = "uq_mhe_oranli"
TEKIL_ORANSIZ = "uq_mhe_oransiz"


def _in_check(sutun: str, kume: tuple[str, ...]) -> str:
    return "%s IN (%s)" % (sutun, ",".join("'" + d + "'" for d in kume))


def upgrade() -> None:
    bind = op.get_bind()
    mevcut = set(sa.inspect(bind).get_table_names())
    if TABLO in mevcut:
        return
    op.create_table(
        TABLO,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("olay", sa.String(length=40), nullable=False),
        sa.Column("kdv_orani", sa.Numeric(9, 4), nullable=True),
        sa.Column("taraf_tipi", sa.String(length=20), nullable=True),
        sa.Column("hesap_kodu", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_in_check("olay", OLAYLAR), name="ck_mhe_olay"),
        sa.CheckConstraint(
            "kdv_orani IS NULL OR (kdv_orani >= 0 AND kdv_orani <= 100)",
            name="ck_mhe_kdv_orani",
        ),
        sa.CheckConstraint(
            "taraf_tipi IS NULL OR " + _in_check("taraf_tipi", TARAF_TIPLERI),
            name="ck_mhe_taraf_tipi",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        # 0062'nin kurali: bilesik yabanci anahtar HEDEFI olabilmesi icin.
        sa.UniqueConstraint("company_id", "id", name=FIRMA_KIMLIK),
    )
    op.create_index(
        TEKIL_ORANLI,
        TABLO,
        [
            sa.text("company_id"),
            sa.text("olay"),
            sa.text("kdv_orani"),
            sa.text("COALESCE(taraf_tipi, '')"),
        ],
        unique=True,
        sqlite_where=sa.text("kdv_orani IS NOT NULL"),
        postgresql_where=sa.text("kdv_orani IS NOT NULL"),
    )
    op.create_index(
        TEKIL_ORANSIZ,
        TABLO,
        [
            sa.text("company_id"),
            sa.text("olay"),
            sa.text("COALESCE(taraf_tipi, '')"),
        ],
        unique=True,
        sqlite_where=sa.text("kdv_orani IS NULL"),
        postgresql_where=sa.text("kdv_orani IS NULL"),
    )


def downgrade() -> None:
    """Indeksler tabloyla birlikte duser (iki lehcede de)."""
    bind = op.get_bind()
    if TABLO in set(sa.inspect(bind).get_table_names()):
        op.drop_table(TABLO)
