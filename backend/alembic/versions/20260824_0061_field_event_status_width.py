"""`field_integration_events.status` DAR — 26 karakterlik durum 20'ye sığmıyor.

Konu: mobil-erp#95 (FAZ 4 outbox zamanlayıcısı).

--- ÖLÇÜLEN KUSUR ------------------------------------------------------------

Sütun 0044'te ``VARCHAR(20)`` açıldı; o gün yazılan tek durum ``PENDING``di.
FAZ 4 tüketicisi kovaları ADLANDIRDI ve adlardan biri sütuna SIĞMIYOR:

    SKIPPED_SOURCE_NOT_VISIBLE   26 karakter   > 20  ← taşıyor
    SKIPPED_NO_PRODUCT           19 karakter   < 20  ← sınırın BİR ALTINDA

Gerçek PostgreSQL 16.4'te ölçüldü: kaynağı görünmeyen bir olayı sonlandırma
denemesi ``psycopg.errors.StringDataRightTruncation`` veriyor. İşlem geri
alınır, olay ``PENDING`` kalır ve SONRAKİ HER DÖNGÜ aynı olayda yeniden
çöker — o kiracının kuyruğu KALICI olarak durur. SQLite `VARCHAR` uzunluğunu
YOK SAYAR; bu yüzden mevcut testlerin hepsi yeşildi.

--- NEDEN GENİŞLETME, NEDEN 64 ----------------------------------------------

Sorun bir DEĞERİN uzunluğu değil, SINIRIN kendisi: 19 karakterlik
``SKIPPED_NO_PRODUCT`` sınırın bir karakter altındaydı, yani bir sonraki
adlandırılmış kova da aynı tuzağa düşecekti. Sütun bu yüzden değerin
uzunluğuna değil, ADLANDIRMA ALIŞKANLIĞINA göre boyutlanıyor: en uzun mevcut
durum 26, tavan 64 — pay açık bırakıldı.

Sınırın kendisi ayrıca `tests/test_field_stok_durum_genisligi.py` içinde
BİLDİRİMLERDEN ve ŞEMADAN türetilerek dondurulur: sütuna sığmayan yeni bir
durum sabiti eklemek, kimse testi düzenlemeden, testi KIRMIZI yapar.

SQLite'ta uzunluk zaten anlamsız; buna rağmen orada da uygulanıyor, çünkü
şemanın BİLDİRDİĞİ tip iki arka uçta AYNI olmalı — testin okuduğu şema odur.

--- GERİ ALMA SIRASI — GECE 3'TE OKUNACAK BİÇİMDE ----------------------------

ÖNCE ŞUNU BİL: **YALNIZ İMAJI GERİ ALMAK AÇILMAZ.**

docker-compose.prod.yml, ``APP_IMAGE_TAG``i önceki commit SHA'sına çevirmeyi
"geri dönüşün tek güvenilir yolu" diye anlatır. Bu göç UYGULANDIKTAN sonra o
adım TEK BAŞINA uygulamayı BAŞLATMAZ. Sebep şema değil, REVİZYON DEFTERİDİR:
``app/main.py`` göçleri IMPORT ANINDA koşturur ve eski imajın
``alembic/versions/`` dizininde ``20260824_0061`` YOKTUR.

ÖLÇÜLDÜ (2026-08-26; veritabanı 20260824_0061'de, imaj 0061'i TAŞIMAYAN bir
ağaç — ikisi de bu depodan):

* ``AUTO_MIGRATE=true`` (ÜRETİM VARSAYILANI; compose bu değeri taşır)::

      alembic.util.exc.CommandError:
          Can't locate revision identified by '20260824_0061'

* ``AUTO_MIGRATE=false``::

      RuntimeError: AUTO_MIGRATE=false fakat veritabanı şeması güncel değil:
          current='20260824_0061', expected='20260821_0060'

* KONTROL — AYNI eski imaj, veritabanı ``20260821_0060``ta iken: AÇILIYOR.
  Yani açılmama, imajın kendisinden değil defterdeki revizyondan geliyor.

SIRA (varsayılan yol):

1. Trafiği kes.
2. **KAYIP KONTROLÜ.** Geri alma, 20 karakteri aşan durumları ``'DEAD'``
   yazar::

       SELECT count(*) FROM field_integration_events
       WHERE length(status) > 20;

   **0 ise** geri alma KAYIPSIZDIR — tüketici hiç açılmadıysa beklenen budur
   (bugün 20'yi aşan tek durum ``SKIPPED_SOURCE_NOT_VISIBLE``).
   **0 değilse** o satırlar ``'DEAD'`` olur; ``DEAD`` TERMİNALDİR ve onu
   ``PENDING``e döndüren bir yol BUGÜN YOKTUR. Ya önce yedek al, ya aşağıdaki
   KAYIPSIZ SEÇENEĞİ kullan.
3. Göçü **YENİ imajla** geri al — eski imaj bunu YAPAMAZ, 0061 onda yok::

       alembic downgrade 20260821_0060

4. **ŞİMDİ** ``APP_IMAGE_TAG``i önceki SHA'ya çevir ve ``up -d``.

SIRAYI TERS ÇEVİRME: önce imajı geri alırsan elinde göçü geri alabilecek
konteyner KALMAZ (yeni imaj gitmiştir, eski imaj 0061'i çözemez) ve uygulama
açılmadığı için sana kabuk da vermez. O noktada çıkış, yeni imajı tek
seferlik ``docker run`` ile çağırıp 3. adımı koşturmaktır.

KAYIPSIZ SEÇENEK (2. adımdaki sayım 0 DEĞİLSE). Genişletme İLERİ UYUMLUDUR:
eski kod bu sütuna yalnız ``'PENDING'`` yazar ve ``VARCHAR(64)`` onu sorunsuz
alır. Yani sütunu GENİŞ bırakıp yalnız defteri geri sarmak yeter::

    UPDATE alembic_version SET version_num='20260821_0060';

ÖLÇÜLDÜ (aynı gün, aynı ikili): eski imaj bundan sonra AÇILIYOR ve 26
karakterlik satır ``'DEAD'`` olmadan DURUYOR. Bedeli, bildirilen şema
(0060) ile gerçek sütunun (VARCHAR(64)) AYRIŞMASIDIR; ileri çıkarken
``upgrade head`` 0061'i yeniden koşturur ve zaten 64 olan sütunu yeniden 64
yapmak zararsızdır. Bu seçenek VERİYİ, varsayılan yol ise ŞEMA DÜRÜSTLÜĞÜNÜ
korur; hangisinin daha pahalı olduğuna 2. adımdaki sayıya bakarak karar ver.

Bu göçün gidiş-dönüşü ölçülüdür: ``tests/test_field_stok_0061_gidis_donus.py``
(veri kolu ve sınırın iki tarafı) ve ``test_field_stok_0061_postgresql.py``
(gerçek PG'de daraltmanın veri koluna MUHTAÇ olduğu).
"""
import sqlalchemy as sa
from alembic import op

revision = "20260824_0061"
down_revision = "20260821_0060"
branch_labels = None
depends_on = None

TABLO = "field_integration_events"
SUTUN = "status"
YENI = 64
ESKI = 20


def _genislet(uzunluk: int, eski_uzunluk: int) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite'ta tablo yeniden kurulur; batch modu indeksleri ve
        # kısıtları yansımadan geri yazar.
        with op.batch_alter_table(TABLO) as batch:
            batch.alter_column(
                SUTUN,
                existing_type=sa.String(length=eski_uzunluk),
                type_=sa.String(length=uzunluk),
                existing_nullable=False,
                existing_server_default="PENDING",
            )
        return
    op.alter_column(
        TABLO, SUTUN,
        existing_type=sa.String(length=eski_uzunluk),
        type_=sa.String(length=uzunluk),
        existing_nullable=False,
        existing_server_default="PENDING",
    )


def upgrade() -> None:
    _genislet(YENI, ESKI)


def downgrade() -> None:
    # DARALTMA VERİ KAYBEDEBİLİR: 20 karakteri aşan durumlar bu göçten sonra
    # yazılmış olabilir. Geri alma, daraltmadan ÖNCE onları görünür bir
    # artığa indirger; sessiz kesme yerine adı konmuş bir kayıp.
    op.execute(
        "UPDATE %s SET %s = 'DEAD' WHERE length(%s) > %d"
        % (TABLO, SUTUN, SUTUN, ESKI)
    )
    _genislet(ESKI, YENI)
