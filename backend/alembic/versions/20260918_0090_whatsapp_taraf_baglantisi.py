"""WHATSAPP TARAF BAGLANTISI: numara -> CARI (musteri/tedarikci) defteri (F10-1a).

Revision ID: 20260918_0090
Revises: 20260915_0089

Konu: bu goc SEMAYI kurar. Servis `app/whatsapp/taraf.py`de, uclar
`app/routers/whatsapp.py`nin `party-` bolumundedir. Tasarimin tamami
`docs/f10-1-ciftci-selfservice-kesif-2026-09-17.md` §3 ve §6.1'dedir.

--- OLCULEN EKSIK: NUMARA YALNIZ PERSONELE COZULUYOR ---------------------

0079'un `whatsapp_links` satiri `(company_id, user_id)` tasir; yani bir
numara bugun YALNIZ bir `app_users` satirina cozulebilir. Ciftci bir
kullanici DEGILDIR — bir CARIDIR. Ve kesif §3.1 olctu: ciftci TEK bir cari
bile degil, IKI ayri caridir (ekstre/ciftlik tarafi `customers`, avans ve
mustahsil makbuzu tarafi `suppliers`) ve ikisi birbirine bagli degildir.

Bu goc o yuzden `customer_id` DEGIL, `notification_consents` (0033) ile
BIREBIR ayni polimorfik bicimi aciyor: `(party_type, party_id)`, sozluk
`consents.PARTY_TYPES` = {'CUSTOMER','SUPPLIER'}.

--- NEDEN `whatsapp_links`E SUTUN EKLENMEDI (kesif §3.2, secenek A) -------

`whatsapp_links.user_id` NOT NULL'dur ve ONBIR yerde `kimlik.user_id` olarak
cozuluyor (8'i yazma/taslak yolunda: `payments.created_by`). Nullable yapmak
o onbir yeri yeniden akil yurutmeye zorlardi. Ayrica
`uq_whatsapp_links_aktif_numara` personel ve ciftciyi AYNI kovaya sokardi ve
`whatsapp_pending_actions`in bilesik FK'si ciftci satirina da asilabilir
hale gelirdi. Ayri tablo, personel yoluna SIFIR dokunus demektir.

--- `party_id` GERCEK FK TASIMAZ -----------------------------------------

Polimorfik bir sutun iki tabloya birden FK ALAMAZ. `notification_consents`
ile AYNI durum ve AYNI cozum: CHECK + uygulama dogrulamasi (`taraf.py`,
tarafin AYNI FIRMADA var ve AKTIF oldugunu kod uretilirken VE kullanilirken
ayri ayri sorar) + geri yukleme siniflandiricisi
(`kiraci_geri_yukleme.AYIRT_EDICI_HEDEFLER`).

--- IKI TABLO DA KIRACI TABLOSUDUR: `TENANT_TABLES` 125 -> 127 -------------

Ikisi de "bu numara HANGI firmanin HANGI carisi" sorusunun cevabidir;
`company_id` dusseydi cevap kuresel olurdu. Ikisinde de `UNIQUE(company_id,
id)` var (0062'nin kurali: bilesik FK hedefi). Kod defterinin
`consumed_link_id`si bu sayede `(company_id, consumed_link_id) ->
whatsapp_party_links(company_id, id)` olarak bagli: bir firmanin kodu BASKA
firmanin baglantisini tuketmis gorunemez.

--- AKTIF NUMARA TEKILLIGI: `(company_id, phone) WHERE is_active` --------

0079'un kurali BIREBIR ve gerekcesi de ayni (kesif §2.3):

  1. Bir numara bir firmada EN FAZLA BIR aktif taraf baglantisi tasir.
     Hakem uygulama sorgusu DEGIL bu kismi indekstir.
  2. Ayni numara FARKLI firmalarda aktif OLABILIR: bir ciftci iki alim
     merkezine urun verir.

KISMI, DUZ DEGIL: pasif satirlar anahtarin DISINDA kalir, yani ayni numara
ayni firmada iki kez KAPATILABILIR ve iz silinmez. Yuklem diyalekte gore
AYRI yazilir (`is_active = 1` / `is_active = true`) — 0079'un
`_aktif_yuklem` gerekcesi. PG ikizi ZORUNLU (`test_f10_1a_taraf_baglantisi_
postgresql.py`): yuklem iki lehcede ayri uretiliyor ve SQLite hatti PG'nin
yuklemini HIC gormez.

--- KOD DEFTERI: 0079'UN YEDI CHECK'I, TARAF UCLUSUYLE -------------------

Durum x zaman damgasi matrisi 0079 ile AYNI; `ck_wppc_*` onekiyle yeniden
yazildi. Ek iki CHECK: `party_type` sozlugu ve `target_phone <> ''`.
`target_phone` 0082'deki gibi `server_default=''` ALMAZ: o varsayilan eski
satirlari doldurmak icindi, bu tablo bos doguyor. Bos hedef bir sozlesme
ihlalidir ve CHECK onu REDDEDER.

Taraf basina EN FAZLA BIR bekleyen kod: `(company_id, party_type, party_id)
WHERE status = 'PENDING'` — 0079'un `uq_wpc_aktif_kod` deseni.

--- BU GOCUN YAPMADIGI -----------------------------------------------------

Dagitici (`service._mesaj_isle`) DEGISMEZ; ciftci niyetleri, KVKK ilk mesaj
sorusu ve numara basina mesaj hiz siniri F10-1b'nin isidir (goc 0091).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260918_0090"
down_revision = "20260915_0089"
branch_labels = None
depends_on = None

BAGLANTI = "whatsapp_party_links"
KOD = "whatsapp_party_pairing_codes"

#: `app/notifications/consents.py::PARTY_TYPES` ile BIREBIR ayni (sirali).
TARAF_TIPLERI = ("CUSTOMER", "SUPPLIER")
#: 0079'un `KOD_DURUMLARI` ile BIREBIR ayni.
KOD_DURUMLARI = ("PENDING", "CONSUMED", "CANCELLED", "EXPIRED")

BAGLANTI_FIRMA_KIMLIK = "uq_whatsapp_party_links_company_id"
BAGLANTI_AKTIF_TEKIL = "uq_whatsapp_party_links_aktif_numara"
BAGLANTI_NUMARA_INDEKS = "ix_whatsapp_party_links_phone"
BAGLANTI_TARAF_INDEKS = "ix_whatsapp_party_links_taraf"

KOD_FIRMA_KIMLIK = "uq_wppc_company_id"
KOD_OZET_TEKIL = "uq_wppc_code_digest"
KOD_AKTIF_TEKIL = "uq_wppc_aktif_kod"
KOD_TARAMA_INDEKS = "ix_wppc_company_status"


def _in_check(sutun: str, degerler: tuple[str, ...]) -> str:
    return "%s IN (%s)" % (sutun, ",".join("'" + d + "'" for d in degerler))


def _aktif_yuklem(lehce: str):
    """Kismi indeksin WHERE'i — DIYALEKTE GORE AYRI (0079'un gerekcesi)."""
    return sa.text("is_active = true" if lehce == "postgresql" else "is_active = 1")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    mevcut = set(inspector.get_table_names())

    if BAGLANTI not in mevcut:
        op.create_table(
            BAGLANTI,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("party_type", sa.String(length=12), nullable=False),
            # POLIMORFIK — gercek FK YOK (baslik).
            sa.Column("party_id", sa.Integer(), nullable=False),
            # Kanonik numara (`telefon.normalize_phone`): ARTI ISARETSIZ
            # rakamlar. `whatsapp_inbound.sender_phone` ile AYNI genislik ve
            # bicim — dagitici iki degeri DOGRUDAN karsilastiracak.
            sa.Column("phone", sa.String(length=20), nullable=False),
            sa.Column(
                "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
            ),
            # Baglanti acildigi anda yazilan riza kaydinin zamani (§5.4).
            # KARAR VERICI DEGILDIR: karar her seferinde
            # `notification_consents`ten yeniden okunur (consents.py
            # sozlesme 2). Bu sutun yalniz "baglanti hangi riza anina
            # dayaniyor" izidir.
            sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            # Kodu ureten personel. Kullanici silinince iz KALIR (SET NULL).
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.CheckConstraint(
                _in_check("party_type", TARAF_TIPLERI), name="ck_wpl_party_type"
            ),
            sa.CheckConstraint("party_id > 0", name="ck_wpl_party_id"),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(
                ["created_by"], ["app_users.id"], ondelete="SET NULL"
            ),
            sa.UniqueConstraint("company_id", "id", name=BAGLANTI_FIRMA_KIMLIK),
        )
        # `taraf_coz` numaradan BUTUN firmalari tarar (0079 `kimlik_coz` ile
        # ayni gerekce): yuklem YALNIZ `phone`dur.
        op.create_index(BAGLANTI_NUMARA_INDEKS, BAGLANTI, ["phone"])
        # Cari kartinin "bu tarafin baglantilari" okumasi.
        op.create_index(
            BAGLANTI_TARAF_INDEKS, BAGLANTI, ["company_id", "party_type", "party_id"]
        )
        op.create_index(
            BAGLANTI_AKTIF_TEKIL,
            BAGLANTI,
            ["company_id", "phone"],
            unique=True,
            sqlite_where=_aktif_yuklem("sqlite"),
            postgresql_where=_aktif_yuklem("postgresql"),
        )

    if KOD not in mevcut:
        op.create_table(
            KOD,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("party_type", sa.String(length=12), nullable=False),
            sa.Column("party_id", sa.Integer(), nullable=False),
            # KOD BIR NUMARAYA VERILIR (SEC-1 / 0082 kurali). Bicim
            # `whatsapp_party_links.phone` ile BIREBIR.
            sa.Column("target_phone", sa.String(length=20), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=True),
            # DUZ KOD ASLA SAKLANMAZ: yalniz SHA-256 hex ozeti.
            sa.Column("code_digest", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=12), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("consumed_link_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                _in_check("party_type", TARAF_TIPLERI), name="ck_wppc_party_type"
            ),
            sa.CheckConstraint("target_phone <> ''", name="ck_wppc_target_phone"),
            # --- 0079'UN YEDI CHECK'I: DURUM x ZAMAN DAMGASI MATRISI -------
            sa.CheckConstraint(
                _in_check("status", KOD_DURUMLARI), name="ck_wppc_status"
            ),
            sa.CheckConstraint("attempt_count >= 0", name="ck_wppc_attempt_count"),
            sa.CheckConstraint("max_attempts > 0", name="ck_wppc_max_attempts"),
            sa.CheckConstraint(
                "(status <> 'PENDING') OR "
                "(consumed_at IS NULL AND cancelled_at IS NULL "
                "AND consumed_link_id IS NULL)",
                name="ck_wppc_pending_temiz",
            ),
            sa.CheckConstraint(
                "(status <> 'CONSUMED') OR "
                "(consumed_at IS NOT NULL AND consumed_link_id IS NOT NULL "
                "AND cancelled_at IS NULL)",
                name="ck_wppc_consumed_alanlari",
            ),
            sa.CheckConstraint(
                "(status <> 'CANCELLED') OR "
                "(cancelled_at IS NOT NULL AND consumed_at IS NULL "
                "AND consumed_link_id IS NULL)",
                name="ck_wppc_cancelled_alani",
            ),
            sa.CheckConstraint(
                "(status <> 'EXPIRED') OR "
                "(consumed_at IS NULL AND consumed_link_id IS NULL "
                "AND cancelled_at IS NULL)",
                name="ck_wppc_expired_temiz",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(
                ["created_by"], ["app_users.id"], ondelete="SET NULL"
            ),
            # BILESIK: bir firmanin kodu BASKA firmanin baglantisini tuketmis
            # gorunemez (baslik).
            sa.ForeignKeyConstraint(
                ["company_id", "consumed_link_id"],
                ["%s.company_id" % BAGLANTI, "%s.id" % BAGLANTI],
            ),
            # KURESEL tekil — 0079 ile ayni gerekce: ozet firma bilinmeden
            # aranir; kiraci kapsamli bir tekil ayni ozeti iki firmada
            # tasiyabilir ve arama IKI satir donerdi.
            sa.UniqueConstraint("code_digest", name=KOD_OZET_TEKIL),
            sa.UniqueConstraint("company_id", "id", name=KOD_FIRMA_KIMLIK),
        )
        op.create_index(
            KOD_TARAMA_INDEKS, KOD, ["company_id", "status", "expires_at"]
        )
        # Taraf basina EN FAZLA BIR bekleyen kod — hakem bu indekstir.
        op.create_index(
            KOD_AKTIF_TEKIL,
            KOD,
            ["company_id", "party_type", "party_id"],
            unique=True,
            sqlite_where=sa.text("status = 'PENDING'"),
            postgresql_where=sa.text("status = 'PENDING'"),
        )


def downgrade() -> None:
    """Simetrik ve KOSULLU. SIRA TERS: once kod defteri (bilesik FK ile
    baglantiya bagli), sonra baglanti — 0079'un gerekcesi."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    mevcut = set(inspector.get_table_names())

    if KOD in mevcut:
        indeksler = {i["name"] for i in inspector.get_indexes(KOD)}
        for ad in (KOD_AKTIF_TEKIL, KOD_TARAMA_INDEKS):
            if ad in indeksler:
                op.drop_index(ad, table_name=KOD)
        op.drop_table(KOD)

    if BAGLANTI in mevcut:
        indeksler = {i["name"] for i in inspector.get_indexes(BAGLANTI)}
        for ad in (BAGLANTI_AKTIF_TEKIL, BAGLANTI_TARAF_INDEKS, BAGLANTI_NUMARA_INDEKS):
            if ad in indeksler:
                op.drop_index(ad, table_name=BAGLANTI)
        op.drop_table(BAGLANTI)
