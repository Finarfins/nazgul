"""CS2 ÇEK/SENET ↔ CARİ: karşılıksız çek borç belgesi ve ciro anahtarı.

Revision ID: 20260914_0086
Revises: 20260914_0085

Kaynak: ``docs/cek-senet-kesif-2026-09-10.md`` §3.2 seçenek (b) ve şefin CS2
kararı (``docs/prompts/CLAUDE-ANAPC-CS2-KARAR.txt``). Yeni TABLO YOK;
``TENANT_TABLES`` değişmez.

--- ÖLÇÜLEN ENGEL: İKİ CHECK ``bounced_check``İ REDDEDİYOR -------------------

0041 ``receivable_charge_documents`` üzerine iki kapalı kısıt koydu:

* ``ck_receivable_charge_document_type``: ``charge_type IN ('late_fee',
  'service_fee')`` — ``'bounced_check'`` satırı eklenemez.
* ``ck_receivable_charge_document_source_shape``: her satır ya bir vade farkı
  (dönem + sipariş + oran anlık görüntüleri DOLU) ya da bir servis borcudur
  (iş emri DOLU). Karşılıksız çekin siparişi, dönemi, iş emri YOKTUR; iki dala
  da uymaz.

Sahte bir sipariş/dönem ya da iş emriyle ``late_fee``/``service_fee`` kılığına
sokmak yaşlandırmayı ve tahsisi bozardı. Bu göç iki kısıtı ÜÇÜNCÜ bir dalla
genişletir: ``bounced_check`` satırında ``cek_senet_id`` ZORUNLU, sipariş /
dönem / iş emri ve vade farkı anlık görüntüleri NULL. Eski iki dala
``cek_senet_id IS NULL`` eklenir: bir vade farkı ya da servis borcu bir çeke
bağlanamaz. Onların geri kalan şekli DEĞİŞMEZ.

--- ``cek_senet_id`` VE BİLEŞİK FK --------------------------------------------

``(company_id, cek_senet_id) -> cek_senetler(company_id, id)``; hedef UNIQUE
0085'te kuruldu (``uq_cek_senetler_company_id``). Başka firmanın çekine borç
belgesi bağlamak VERİTABANI SEVİYESİNDE imkânsızdır. ``ON DELETE RESTRICT``:
borç belgesi taşıyan bir çek silinemez (0041'in iş emri FK'sinin aynısı).

--- TEK AKTİF BELGE ------------------------------------------------------------

Belge durumu ÖLÇÜLDÜ (0028: ``status IN ('draft','posted','reversed')``, ters
kayıt ``reversal_of_document_id`` taşır). "Aktif" 0041'in servis borcu
indeksindeki tanımla AYNIDIR: ``status='posted' AND reversal_of_document_id IS
NULL``. ``uq_receivable_bounced_check_active`` bir çek için ikinci aktif borç
belgesini reddeder — iki eşzamanlı "karşılıksız" isteği cariyi iki kez
borçlandıramaz; terslenen belgenin yerine yenisi açılabilir.

--- ``companies.ciro_tedarikci_odemesi`` ---------------------------------------

Karar 3 açık: ciro edilen çek tedarikçiye otomatik ödeme açsın mı? Anahtar
firma başınadır ve VARSAYILANI KAPALIDIR (``server_default false``); 0048'in
``farm_spraying_dose_required`` Boolean kalıbı. Mevcut firmalar KAPALI kalır —
bu bir davranış değişikliği DEĞİLDİR.

--- SQLite: TOPLU YENİDEN KURMA -----------------------------------------------

CHECK değiştirmek SQLite'ta tabloyu yeniden kurar (``batch_alter_table``). Var
olan indeksler yansıtılarak yeniden kurulur; bu ÖLÇÜLÜR, varsayılmaz
(``tests/test_cs2_cek_senet_cari.py::test_GOC_SQLite_indeksleri_KORUNUR``).

--- GERİ ALMA --------------------------------------------------------------------

Ters sırada. ``bounced_check`` satırı varsa REDDEDER (0041'in servis borcu
kalıbı): o satırlar genişletilmemiş kısıtlara sığmaz ve sessizce silinmeleri
cariden borç kaybettirirdi.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260914_0086"
down_revision = "20260914_0085"
branch_labels = None
depends_on = None

BELGE = "receivable_charge_documents"
FIRMA = "companies"
SUTUN = "cek_senet_id"
ANAHTAR = "ciro_tedarikci_odemesi"

CK_TUR = "ck_receivable_charge_document_type"
CK_SEKIL = "ck_receivable_charge_document_source_shape"
FK_CEK = "fk_receivable_charge_document_cek_senet"
UQ_AKTIF = "uq_receivable_bounced_check_active"

#: Kapalı küme. ``app/cek_senet_cari.py::KARSILIKSIZ_CEK`` bu kümenin üçüncü
#: elemanıdır (kapı: ``tests/test_cs2_cek_senet_cari.py``).
TURLER = ("late_fee", "service_fee", "bounced_check")

_VADE_FARKI_ALANLARI = (
    "principal_basis",
    "annual_rate_snapshot",
    "day_count_basis_snapshot",
    "grace_days_snapshot",
    "tax_mode_snapshot",
    "vat_rate_snapshot",
    "net_amount",
    "vat_amount",
)


def _bos(alanlar: tuple[str, ...]) -> str:
    return "\n  ".join(f"AND {a} IS NULL" for a in alanlar)


def _dolu(alanlar: tuple[str, ...]) -> str:
    return "\n  ".join(f"AND {a} IS NOT NULL" for a in alanlar)


SEKIL_0086 = f"""
(
  charge_type = 'late_fee'
  AND work_order_id IS NULL
  AND cek_senet_id IS NULL
  AND charge_period_id IS NOT NULL
  AND order_id IS NOT NULL
  {_dolu(_VADE_FARKI_ALANLARI)}
)
OR
(
  charge_type = 'service_fee'
  AND work_order_id IS NOT NULL
  AND cek_senet_id IS NULL
  AND charge_period_id IS NULL
  AND order_id IS NULL
  {_bos(_VADE_FARKI_ALANLARI)}
)
OR
(
  charge_type = 'bounced_check'
  AND cek_senet_id IS NOT NULL
  AND work_order_id IS NULL
  AND charge_period_id IS NULL
  AND order_id IS NULL
  {_bos(_VADE_FARKI_ALANLARI)}
)
"""

#: 0041'in şekil kısıtı, BİREBİR (geri alma bunu kurar).
SEKIL_0041 = f"""
(
  charge_type = 'late_fee'
  AND work_order_id IS NULL
  AND charge_period_id IS NOT NULL
  AND order_id IS NOT NULL
  {_dolu(_VADE_FARKI_ALANLARI)}
)
OR
(
  charge_type = 'service_fee'
  AND work_order_id IS NOT NULL
  AND charge_period_id IS NULL
  AND order_id IS NULL
  {_bos(_VADE_FARKI_ALANLARI)}
)
"""

AKTIF_KOSUL = (
    "cek_senet_id IS NOT NULL AND status='posted' "
    "AND reversal_of_document_id IS NULL"
)


def _liste(degerler: tuple[str, ...]) -> str:
    return ",".join("'" + d + "'" for d in degerler)


def _adlar(inspector, tablo: str, tur: str) -> set[str]:
    okuyucu = {
        "check": inspector.get_check_constraints,
        "foreign_key": inspector.get_foreign_keys,
        "index": inspector.get_indexes,
    }[tur]
    return {str(k["name"]) for k in okuyucu(tablo) if k.get("name")}


def _sutunlar(inspector, tablo: str) -> set[str]:
    return {str(c["name"]) for c in inspector.get_columns(tablo)}


def upgrade() -> None:
    bind = op.get_bind()

    # --- 1. Borç belgesi: sütun + iki CHECK + bileşik FK -----------------------
    inspector = sa.inspect(bind)
    sutunlar = _sutunlar(inspector, BELGE)
    kisitlar = _adlar(inspector, BELGE, "check")
    yabancilar = _adlar(inspector, BELGE, "foreign_key")
    with op.batch_alter_table(BELGE) as batch:
        if SUTUN not in sutunlar:
            batch.add_column(sa.Column(SUTUN, sa.Integer(), nullable=True))
        for ad in (CK_TUR, CK_SEKIL):
            if ad in kisitlar:
                batch.drop_constraint(ad, type_="check")
        batch.create_check_constraint(CK_TUR, "charge_type IN (%s)" % _liste(TURLER))
        batch.create_check_constraint(CK_SEKIL, sa.text(SEKIL_0086))
        if FK_CEK not in yabancilar:
            batch.create_foreign_key(
                FK_CEK,
                "cek_senetler",
                ["company_id", SUTUN],
                ["company_id", "id"],
                ondelete="RESTRICT",
            )

    # --- 2. Tek aktif belge ------------------------------------------------------
    inspector = sa.inspect(bind)
    if UQ_AKTIF not in _adlar(inspector, BELGE, "index"):
        op.create_index(
            UQ_AKTIF,
            BELGE,
            ["company_id", SUTUN],
            unique=True,
            postgresql_where=sa.text(AKTIF_KOSUL),
            sqlite_where=sa.text(AKTIF_KOSUL),
        )

    # --- 3. Firma anahtarı ------------------------------------------------------
    inspector = sa.inspect(bind)
    if ANAHTAR not in _sutunlar(inspector, FIRMA):
        op.add_column(
            FIRMA,
            sa.Column(ANAHTAR, sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    bind = op.get_bind()
    adet = int(
        bind.execute(
            sa.text(f"SELECT COUNT(*) FROM {BELGE} WHERE charge_type='bounced_check'")
        ).scalar_one()
    )
    if adet:
        raise RuntimeError(
            "0086 geri alınamaz: %d karşılıksız çek borç belgesi (bounced_check) var. "
            "Bu satırlar 0041 kısıtlarına sığmaz ve silinmeleri cariden borç "
            "kaybettirir; önce açıkça arşivlenmeli." % adet
        )

    inspector = sa.inspect(bind)
    if ANAHTAR in _sutunlar(inspector, FIRMA):
        op.drop_column(FIRMA, ANAHTAR)

    inspector = sa.inspect(bind)
    if UQ_AKTIF in _adlar(inspector, BELGE, "index"):
        op.drop_index(UQ_AKTIF, table_name=BELGE)

    inspector = sa.inspect(bind)
    kisitlar = _adlar(inspector, BELGE, "check")
    yabancilar = _adlar(inspector, BELGE, "foreign_key")
    sutunlar = _sutunlar(inspector, BELGE)
    with op.batch_alter_table(BELGE) as batch:
        if FK_CEK in yabancilar:
            batch.drop_constraint(FK_CEK, type_="foreignkey")
        for ad in (CK_SEKIL, CK_TUR):
            if ad in kisitlar:
                batch.drop_constraint(ad, type_="check")
        batch.create_check_constraint(CK_TUR, "charge_type IN ('late_fee','service_fee')")
        batch.create_check_constraint(CK_SEKIL, sa.text(SEKIL_0041))
        if SUTUN in sutunlar:
            batch.drop_column(SUTUN)
