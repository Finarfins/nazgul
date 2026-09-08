"""WHATSAPP GİRİŞİ: Meta webhook'unun yazdığı PLATFORM kuyruğu (WA1).

Konu: bu göç ŞEMAYI kurar. Uçlar `app/routers/whatsapp.py`de, yazma
davranışı `app/whatsapp/giris.py`de, gövde çözümlemesi ve HMAC
`app/whatsapp/cloud_api.py`dedir.

--- ÖLÇÜLEN EKSİK: NİYET VAR, GİRİŞ YOK ---------------------------------

WA3 (`app/whatsapp/`) niyet çözücüyü ve Harman köprüsünü getirdi ve
başlığında bunu AÇIKÇA söylüyor: "veritabanı yok, tablo yok, rota yok".
Yani depoda bir WhatsApp mesajını KARŞILAYAN hiçbir şey yoktu — ölçüldü:
`app/routers/` altında `whatsapp` adında bir modül YOKTU ve
`alembic/versions/` altında `wamid` sütunu taşıyan tek bir tablo bile
YOKTU. Niyet çözücü, kendisine mesaj getirecek yolu bekliyordu.

Bu göç o yolun KALICI UCUNU açıyor: webhook'un yazdığı satırı.

--- İKİ TABLO DA PLATFORM TABLOSUDUR: `company_id` YOK -------------------

Bu, bu göçün EN ÖNEMLİ kararıdır ve bir ihmal DEĞİLDİR.

Webhook'a gelen bir mesaj HENÜZ HİÇBİR FİRMAYA AİT DEĞİLDİR. Meta'nın
gövdesinde güvenilebilir tek şey imzadır; içindeki `from` numarası bir
KİMLİK İDDİASI değil, bir dizedir. "Bu numara hangi firmanın kullanıcısı"
sorusunun cevabı bu depoda HENÜZ YOKTUR (eşleştirme defteri bu PR'ın
KAPSAMINDA DEĞİL) ve olsaydı bile cevap webhook'ta değil, İŞÇİDE
çözülürdü.

`company_id` sütunu koysaydık iki kötü seçenekten birine mahkûm olurduk:
ya NULL bırakırdık — o zaman kiracı nöbetçisi tabloyu kiracı tablosu
sayar ve her sorgudan `company_id=:cid` yüklemi ister, yüklemin karşılığı
OLMADIĞI hâlde — ya da gövdedeki numaradan TAHMİN ederdik ki bu tam
olarak "payload'daki yetki iddiasına güvenmek" olurdu.

Bu yüzden iki tablo da `app_users`/`companies`/`auth_refresh_tokens` ile
AYNI sınıftadır: PLATFORM tabloları. `TENANT_TABLES` envanteri elle
yazılmış bir muafiyet listesi DEĞİLDİR; `company_id` sütunu taşıyan
tabloları göç edilmiş şemadan OTOMATİK tarar
(`tests/test_tenant_scoping_guard.py::
test_tenant_table_inventory_matches_migrated_schema`). Yani bu iki tablo
oraya GİRMEZ ve envanter 116'da SABİT kalır — sütunun YOKLUĞU muafiyetin
KENDİSİDİR ve bayatlayacak bir liste satırı yoktur.

--- `wamid` UNIQUE: IDEMPOTENCY'NİN KENDİSİ ------------------------------

Meta AYNI teslimatı birden çok kez gönderir (2xx'i geç alırsa ya da hiç
alamazsa). Kopyayı "önce SELECT sonra INSERT" ile elemek YARIŞA AÇIKTIR:
iki eşzamanlı webhook çağrısı ikisi de "yok" görüp ikisi de yazabilir.
Hakem bu yüzden UYGULAMA DEĞİL, VERİTABANI KISITIDIR — `uq_whatsapp_
inbound_wamid`. Yazan taraf `IntegrityError`ı yutar ve yine 200 döner.

--- `status` KAPALI KÜME, BEŞ DEĞER --------------------------------------

`RECEIVED` → webhook yazdı, kimse almadı. `PROCESSING` → bir işçi lease
aldı. `ANSWERED` → cevap üretildi. `IGNORED` → sessiz son (bilinmeyen
numara, boş metin). `DEAD` → kalıcı hata; yeniden denenmez.

BU PR'DA YALNIZ `RECEIVED` YAZILIR. Öteki dördü şemada bugünden duruyor
çünkü `status` sütununun genişliği ve CHECK'i bir GÖÇ işidir; işçiyi
getiren PR'ın şemayı ikinci kez oynatması gerekmesin diye. Kümenin
kapalılığı CHECK ile veritabanı seviyesinde ısırıyor: tanınmayan bir
değer, hiçbir işçinin almadığı sessiz bir ölü satır üretirdi.

--- `whatsapp_pairing_attempts`: NUMARA BAŞINA PENCERE SAYACI ------------

İkinci tablo bir SAYAÇTIR ve bu PR'da HİÇ YAZILMAZ. Neden bugün açıldığı
ölçülebilir: eşleştirme akışı (numarayı bir kullanıcıya bağlayan "BAĞLA
<KOD>" mesajı) rastgele kod denemelerine karşı numara başına bir pencere
sayacı ister ve o sayaç UYGULAMA BELLEĞİNDE OLAMAZ — çok konteynerli bir
kurulumda bellek paylaşılmaz. Sayacın VERİTABANINDA olması bir şema
kararıdır; onu eşleştirme PR'ına ertelemek, o PR'ı ikinci bir göçe mahkûm
ederdi.

`(phone, window_start)` UNIQUE: aynı numara aynı pencerede TEK satırdır ve
artış o satırın üzerinde yapılır. Tekil olmasaydı iki eşzamanlı deneme iki
satır üretir ve sayaç ikisini de "1" sayardı — yani sınır HİÇ ısırmazdı.

--- BU GÖÇÜN YAPMADIĞI: İŞLEME, CEVAP, EŞLEŞTİRME — ÖLÇÜLMEDİ ------------

Bu turda işçi YOKTUR, cevap ÜRETİLMEZ, giden mesaj GÖNDERİLMEZ ve numara
hiçbir kullanıcıya BAĞLANMAZ. Satırlar `RECEIVED` durumunda kalır ve orada
kalmaları BİLİNÇLİDİR. `whatsapp_links`, `whatsapp_context` ve
`whatsapp_pending_actions` bu göçte YOK: onların hepsi bir KİRACI
sorusuna cevap verir ve kiracı çözümü bu dilimin kapsamında değildir.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260910_0078"
down_revision = "20260909_0077"
branch_labels = None
depends_on = None

KUYRUK = "whatsapp_inbound"
SAYAC = "whatsapp_pairing_attempts"

#: Kapalı küme. Gerekçe başlıkta. `app/whatsapp/schema.py` ile BİREBİR aynı.
DURUMLAR = ("RECEIVED", "PROCESSING", "ANSWERED", "IGNORED", "DEAD")

DURUM_CHECK = "ck_whatsapp_inbound_status"
DENEME_CHECK = "ck_whatsapp_inbound_attempt_count"
TEKIL_WAMID = "uq_whatsapp_inbound_wamid"
DURUM_INDEKS = "ix_whatsapp_inbound_status"

SAYAC_TEKIL = "uq_whatsapp_pairing_attempts_pencere"
SAYAC_CHECK = "ck_whatsapp_pairing_attempts_count"
SAYAC_INDEKS = "ix_whatsapp_pairing_attempts_pencere"


def _durum_check() -> str:
    return "status IN (%s)" % ",".join("'" + d + "'" for d in DURUMLAR)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    mevcut = set(inspector.get_table_names())

    # 0072'de ölçülen kusur (açılış DDL'i tabloyu göçten ÖNCE kurar, göç onu
    # VAR bulup atlar) bu iki tabloda ÜRETİLEMEZ: ikisi de YALNIZ burada
    # doğuyor ve hiçbir `metadata.create_all` çağrısının kapsamında değil.
    # Kapı: `tests/test_wa1_ingress.py::test_ACILIS_DDLi_GOCUN_ONUNE_GECMIYOR`.
    if KUYRUK not in mevcut:
        op.create_table(
            KUYRUK,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            # Meta mesaj kimliği ("wamid.HBg..."). Bugünkü değerler ~60
            # karakter; 128 sağlayıcının uzatmasına pay bırakır ve
            # `uq_whatsapp_inbound_wamid` B-tree satır sınırının çok altında.
            sa.Column("wamid", sa.String(length=128), nullable=False),
            # Kanonik numara (`app/whatsapp/telefon.py::normalize_phone`),
            # yani ARTI İŞARETSİZ rakam dizisi. E.164 en çok 15 rakam taşır;
            # 20 genişlik ileride bir sağlayıcının biçim değiştirmesine pay.
            sa.Column("sender_phone", sa.String(length=20), nullable=False),
            sa.Column("phone_number_id", sa.String(length=32), nullable=False),
            # NOT NULL ve BOŞ STRING geçerli: medya satırında gövde yerine
            # altyazı yazılır ve altyazı çoğu kez boştur. `nullable=True`
            # yapmak, metin yolundaki "gövde bir dizedir" güvencesini de
            # gevşetirdi ve okuyan her yerde NULL denetimi isterdi.
            sa.Column("text", sa.Text(), nullable=False),
            # İKİLİ VERİ DEĞİL, Meta'daki KİMLİĞİ. İndirme ikinci bir
            # kimlik doğrulamalı istektir ve webhook yanıtının içinde
            # yapılamaz: Meta 2xx'i beklerken yavaş bir üçüncü taraf
            # çağrısı yapmak, teslimat tekrarının ta kendisini üretir.
            sa.Column("media_id", sa.String(length=128), nullable=True),
            sa.Column("media_mime", sa.String(length=64), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            # Lease/CAS çifti — bildirim outbox'ındaki desenin AYNISI.
            # Bu PR'da HİÇ YAZILMAZ; işçiyi getiren PR ikinci bir göç
            # açmasın diye bugünden duruyor (gerekçe başlıkta).
            sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("lock_token", sa.String(length=64), nullable=True),
            # Hata SINIFI/kısa metni. İçerik taşımaz.
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(_durum_check(), name=DURUM_CHECK),
            sa.CheckConstraint("attempt_count >= 0", name=DENEME_CHECK),
            # IDEMPOTENCY'NİN KENDİSİ — gerekçe başlıkta.
            sa.UniqueConstraint("wamid", name=TEKIL_WAMID),
        )
        # İşçinin süpürme sorgusu `status='RECEIVED'` ile daralır. İndeks
        # bugün kullanılmıyor (işçi yok) ama şemayla birlikte doğuyor:
        # ikinci bir göç açmamak için.
        op.create_index(DURUM_INDEKS, KUYRUK, ["status"])

    if SAYAC not in mevcut:
        op.create_table(
            SAYAC,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            # Kanonik numara. KİRACIYA BAĞLI DEĞİL ve olamaz: deneme yapan
            # numara henüz hiçbir firmaya ait değildir (başlık).
            sa.Column("phone", sa.String(length=20), nullable=False),
            sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("attempt_count >= 0", name=SAYAC_CHECK),
            sa.UniqueConstraint("phone", "window_start", name=SAYAC_TEKIL),
        )
        # Süpürücü eski pencereleri `window_start` ile siler.
        op.create_index(SAYAC_INDEKS, SAYAC, ["window_start"])


def downgrade() -> None:
    """Simetrik ve KOŞULLU. Tabloları düşürmek VERİ KAYBIDIR ve bilinçlidir.

    Kaybın anlamı ölçülü: silinen şey İŞ VERİSİ DEĞİL, "bu Meta mesajını
    daha önce aldık mı" bilgisidir. Geri alma sonrası Meta'nın yeniden
    teslim ettiği bir mesaj İKİNCİ KEZ işlenebilir hâle gelir — ama bu
    turda işçi YOKTUR, yani bugün geri alma HİÇBİR ikinci işlemeye yol
    açmaz. Deneme sayacının kaybı bir pencerenin sıfırlanmasıdır.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    mevcut = set(inspector.get_table_names())

    if SAYAC in mevcut:
        if SAYAC_INDEKS in {i["name"] for i in inspector.get_indexes(SAYAC)}:
            op.drop_index(SAYAC_INDEKS, table_name=SAYAC)
        op.drop_table(SAYAC)

    if KUYRUK in mevcut:
        if DURUM_INDEKS in {i["name"] for i in inspector.get_indexes(KUYRUK)}:
            op.drop_index(DURUM_INDEKS, table_name=KUYRUK)
        op.drop_table(KUYRUK)
