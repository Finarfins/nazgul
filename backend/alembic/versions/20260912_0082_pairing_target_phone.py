"""Eslestirme kodu HEDEF NUMARAYA baglaniyor: `target_phone`.

Revision ID: 20260912_0082
Revises: 20260911_0081

--- KAPATILAN ACIK: KOD, KIME VERILDIGINI BILMIYORDU --------------------------

Guvenlik incelemesi A/1 (SEC-1, P1) bir CAPRAZ KIRACI DEVRALMA olcumu getirdi
ve olcum bir iddia degildi, kendi seması uzerinde uretildi:

  0079'un actigi `whatsapp_pairing_codes` satiri "hangi firmanin hangi
  kullanicisi" sorusunu cevapliyordu (`company_id` + `user_id`) ama "HANGI
  NUMARA" sorusunu HIC sormuyordu. `eslestirme.kod_kullan` satiri YALNIZ
  `code_digest` ile buluyor ve baglantiyi CAGIRANIN numarasiyla aciyordu.

  Sonuc: B firmasinin kodunu ELE GECIREN biri (ekran goruntusu, iletilmis
  mesaj, omuz ustu) KENDI numarasini B'nin kullanicisina baglayabiliyordu.
  Kod bir SIRDIR ama TEK BASINA bir kimlik degildir; sizan bir sir, sahibinden
  BASKA birinin elinde de ayni kapiyi aciyordu.

Bu goc o soruyu SEMAYA yaziyor: kod artik BIR NUMARAYA verilir ve YALNIZ o
numaradan kullanilabilir. Sizan kod, yanlis ellerde ISE YARAMAZ.

--- SUTUN: `target_phone VARCHAR(20) NOT NULL` --------------------------------

Genislik ve BICIM `whatsapp_links.phone` ile BIREBIR AYNI ve bu zorunludur:
`kod_kullan` iki degeri KARSILASTIRIYOR. Ikisi de `telefon.normalize_phone`
ciktisidir (ulke kodlu, ISARETSIZ rakamlar). Uc (`POST /api/whatsapp/
pairing-codes`) girdiyi `telefon.e164` ile DOGRULAR — yani "0540 599 59 59"
kabul edilir, "abc" ve "12" REDDEDILIR — ama saklanan bicim kanonik
`normalize_phone` ciktisidir. `+90...` saklansaydi karsilastirma her seferinde
bir donusum daha isterdi ve o donusumu unutan bir yol SESSIZCE hicbir kodu
eslestiremezdi.

--- MEVCUT SATIRLAR: GERIYE DOLDURMA YOK, SURESI DOLDURULUYOR -----------------

Tablo 0079 ile (2026-09-10) dogdu ve URETIMDE HIC SATIRI YOK. Yine de bu goc
"satir yok" VARSAYIMIYLA yazilmadi; guvenli yol ACIKCA kodlandi:

  1. Sutun `server_default=''` ile aciliyor. Bos dize BIR NUMARA DEGILDIR ve
     OLAMAZ: `kod_kullan` cagiranin numarasini `normalize_phone`dan geciriyor
     ve BOS sonucta HEMEN donuyor (`if not normal: return`). Yani `''` tasiyan
     bir satir HICBIR cagiranla eslesemez — FAIL-CLOSED.
  2. Buna RAGMEN mevcut `PENDING` satirlarin HEPSI `EXPIRED` yaziliyor.
     (1) tek basina yeterdi; (2) niyeti SEMAYA yaziyor: hedefi bilinmeyen bir
     kod BEKLEYEN sayilmamalidir. Yoneticinin caresi mevcut ve ucuzdur —
     yeni kod uretmek (eskiyi zaten deterministik olarak iptal eder).

`EXPIRED` durumu 0079'un `ck_wpc_expired_temiz` CHECK'ini KIRMAZ: `PENDING`
satirlarin ucu de (`consumed_at`, `cancelled_at`, `consumed_link_id`) zaten
NULL'dur — `ck_wpc_pending_temiz` bunu ZORLUYOR. Iki CHECK ayni satir kumesi
uzerinde ayni sarti istiyor, yani gecis TANIMLIDIR.

--- `server_default` NEDEN DUSURULMUYOR --------------------------------------

PostgreSQL'de `ALTER COLUMN ... DROP DEFAULT` mumkun, SQLite'ta DEGIL: orada
ayni sonuc icin tablo YENIDEN KURULMALI (`batch_alter_table`) ve bu tablonun
KISMI TEKIL indeksleri var (`uq_wpc_aktif_kod`, `sqlite_where` yuklemli).
Yeniden kurma o indeksleri sessizce kaybetme riskini tasir ve kazanc YOK:
varsayilan `''`dir ve `''` FAIL-CLOSED'dur (yukarida).

Varsayilan IKI DIYALEKTTE DE BIRAKILIYOR — birinde dusurulup otekinde
birakilsaydi Alembic'in kurdugu sema iki lehcede AYRISIRDI ve `schema.py`nin
Core tanimi ikisinden YALNIZ BIRIYLE ortusurdu. Core tanim da ayni
`server_default`u tasiyor; goc ile `metadata` ARASINDA fark YOK.

--- GERI ALINABILIR (YAPISAL OLARAK; VERI OLARAK DEGIL) ----------------------

`downgrade()` sutunu dusurur ve up->down->up turu GERCEK PostgreSQL 16'da
kosuluyor (`backend/test_wa2_eslestirme_postgresql.py`).

SURESI DOLDURULAN SATIRLAR GERI GELMEZ ve bu bir eksiklik DEGIL karardir:
onlari `PENDING`e dondurmek, tam da bu gocun kapattigi acigi GERI ACMAK
olurdu. `downgrade` bir sema islemidir; guvenlik olayini geri sarmaz.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


revision = "20260912_0082"
down_revision = "20260911_0081"
branch_labels = None
depends_on = None

KOD = "whatsapp_pairing_codes"
SUTUN = "target_phone"

#: `whatsapp_links.phone` ile AYNI genislik — iki taraf karsilastiriliyor.
GENISLIK = 20

#: Hicbir `normalize_phone` ciktisiyla eslesemeyen deger (FAIL-CLOSED).
BOS = ""


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    mevcut = {c["name"] for c in inspector.get_columns(KOD)}
    if SUTUN not in mevcut:
        op.add_column(
            KOD,
            sa.Column(
                SUTUN,
                sa.String(GENISLIK),
                nullable=False,
                server_default=BOS,
            ),
        )

    # HEDEFI BILINMEYEN BEKLEYEN KOD KALMIYOR. Bos hedef zaten hicbir
    # cagiranla eslesemez; bu adim niyeti SEMAYA yaziyor.
    bind.execute(
        text(
            "UPDATE %s SET status='EXPIRED'"
            " WHERE status='PENDING' AND %s = :bos" % (KOD, SUTUN)
        ),
        {"bos": BOS},
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    mevcut = {c["name"] for c in inspector.get_columns(KOD)}
    if SUTUN in mevcut:
        op.drop_column(KOD, SUTUN)
