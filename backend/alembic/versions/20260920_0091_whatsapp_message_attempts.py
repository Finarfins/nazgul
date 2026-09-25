"""WHATSAPP MESAJ SAYACI: numara basina hiz siniri (F10-1b).

Revision ID: 20260920_0091
Revises: 20260918_0090

Konu: bu goc TEK bir PLATFORM tablosu acar. Yazan tek cagiran
`app/whatsapp/ciftci_yurutucu.mesaj_deneme_say`dir; tasarimin tamami
`docs/f10-1-ciftci-selfservice-kesif-2026-09-17.md` §5.3 ve K6'dadir.

--- OLCULEN BOSLUK: BAGLI NUMARANIN NORMAL MESAJ YOLUNDA SINIR YOK ------

Kesif §1.5 olctu: `app/routers/whatsapp.py` ve `app/whatsapp/service.py`
icinde `rate_limit|hiz_sinir|RateLimit|limiter` HIC gecmiyor. Yani
`BAGLA` disindaki her mesaj bugun SINIRSIZDIR. Bu personel icin kabul
edilebilir (iceriden, sayisi az, kimligi daha dar bir zincirden gecmis);
ciftci icin degildir: dis taraf, sayisi cok ve HER CEVAP Meta'ya ucretli
bir mesaj.

Sinir bu yuzden YALNIZ CIFTCI YOLUNA uygulanir. Personel yolu bu gocten
sonra da sinirsizdir ve bu bir eksiklik degil, KAPSAM karari: personel
sinirini degistirmek onbir cagiranin davranisini degistirirdi ve olculmedi.

--- NEDEN YENI TABLO, MEVCUDA `kind` SUTUNU DEGIL (K6) -----------------

`whatsapp_pairing_attempts`in tekil anahtari `(phone, window_start)`tir.
`kind` eklemek o anahtari `(phone, window_start, kind)` yapmak, yani
YURURLUKTEKI eslestirme sinirini bir goc boyunca ZAYIFLATMAK demektir:
gocun calistigi an ile uygulamanin yeni anahtari kullanmaya basladigi an
arasinda ayni numara iki kovaya birden yazabilirdi. Ayri tablo, mevcut
sinira SIFIR dokunus demektir.

--- `company_id` YOK VE OLMAMASI ZORUNLU -------------------------------

Iki gerekce, ikincisi birincisinden daha kuvvetli:

  1. `whatsapp_pairing_attempts`in gerekcesi: sayilan sey bir NUMARADIR,
     bir firmanin satiri degil.
  2. Sinirin KORUDUGU sey firmanin verisi DEGIL, bot numarasinin mesaj
     butcesidir. `company_id` tasisaydi iki alim merkezine birden bagli
     bir ciftci (ayni numara, iki `whatsapp_party_links` satiri) sinirini
     IKIYE KATLARDI. Kapi: `test_SAYAC_NUMARA_BASINA_FIRMALAR_ARASI`.

Sonuc: tablo `TENANT_TABLES`a GIRMEZ (125 -> 127 F10-1a'da oldu, bu goc
onu DEGISTIRMEZ) ve kiraci disa aktarimi onu gormez — disa aktarim
listesini SEMADAN turetiyor (`routers/kiraci_disa_aktarim._kiraci_tablolari`:
"`company_id` sutunu olan her tablo"), elle yazilmis bir liste YOK.

--- TEKIL KISIT SUSLEME DEGIL, SAYACIN HAKEMIDIR -----------------------

`uq_whatsapp_message_attempts_pencere` olmasaydi iki es zamanli isci iki
satir uretir ve sinir HIC ISIRMAZDI. `mesaj_deneme_say` tek deyimlik bir
UPSERT'tir ve bu kisit onun cakisma HEDEFIDIR (PG: `ON CONFLICT`,
SQLite: `ON CONFLICT`) — uygulama bellegi KULLANILMAZ, cok konteynerde
paylasilmaz.

--- GERI ALMA -----------------------------------------------------------

Simetrik ve KOSULLU. Dusen sey IS VERISI DEGIL, 15 dakikalik bir
sayactir: geri alma sonrasi sinir sifirlanir, yani en kotu ihtimalle bir
pencere boyunca daha fazla mesaj islenir. Kalici hicbir kayip yok.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260920_0091"
down_revision = "20260918_0090"
branch_labels = None
depends_on = None

SAYAC = "whatsapp_message_attempts"
SAYAC_TEKIL = "uq_whatsapp_message_attempts_pencere"
SAYAC_INDEKS = "ix_whatsapp_message_attempts_pencere"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if SAYAC in set(inspector.get_table_names()):
        return

    op.create_table(
        SAYAC,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        # KIRACIYA BAGLI DEGIL (baslik). `whatsapp_party_links.phone` ve
        # `whatsapp_inbound.sender_phone` ile AYNI genislik ve AYNI bicim
        # (`telefon.normalize_phone`): sayac dagiticinin elindeki degerle
        # DOGRUDAN anahtarlanir, bir donusum daha araya girmez.
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_whatsapp_message_attempts_count"
        ),
        # UPSERT'in CAKISMA HEDEFI (baslik). Sussuz bir tablo sinirsiz
        # tabloyla AYNI seydir.
        sa.UniqueConstraint("phone", "window_start", name=SAYAC_TEKIL),
    )
    # Pencere taramasi (temizlik/olcum) icin; tekil anahtarin onu `phone`
    # oldugu icin `window_start` tek basina indekslenmemis kalirdi.
    op.create_index(SAYAC_INDEKS, SAYAC, ["window_start"])


def downgrade() -> None:
    """Simetrik ve KOSULLU (baslik: kaybedilen sey 15 dakikalik bir sayac)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if SAYAC not in set(inspector.get_table_names()):
        return

    if SAYAC_INDEKS in {i["name"] for i in inspector.get_indexes(SAYAC)}:
        op.drop_index(SAYAC_INDEKS, table_name=SAYAC)
    op.drop_table(SAYAC)
