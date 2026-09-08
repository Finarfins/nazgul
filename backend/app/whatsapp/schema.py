"""WA1 giriş tabloları (Core, ORM'siz) — İKİSİ DE PLATFORM TABLOSU.

Kaynak `nazgul_website/backend/app/whatsapp/schema.py`. Oradaki ALTI
tablodan bu depoya YALNIZ İKİSİ taşındı ve ikisi de DEĞİŞTİRİLEREK: kaynağın
`whatsapp_inbound`u `company_id`/`user_id` sütunları taşıyordu, bu depoda
TAŞIMIYOR. Gerekçe göçün başlığındadır (`20260910_0078`): webhook'a gelen
mesaj henüz hiçbir firmaya ait değildir ve bu depoda kiracı sınırı bir
NÖBETÇİYLE korunuyor — `company_id` taşıyan her tablo `TENANT_TABLES`a
girer ve her sorgusundan `company_id=:cid` yüklemi istenir. Karşılığı
olmayan bir yüklem, kuralı hem yalan hem işlevsiz yapardı.

TAŞINMAYANLAR ve gerekçeleri (hepsi bu dilimin KAPSAMI DIŞINDA):

* `whatsapp_links`          — "bu numara KİM?" Eşleştirme defteri; kiracı
  tablosudur ve eşleştirme akışı bu PR'da YOK.
* `whatsapp_context`        — "az önce ne konuşuyorduk?" İşçi olmadan
  yazılacak bağlam da yoktur.
* `whatsapp_pending_actions`— iki adımlı yazma taslakları; bu PR hiçbir
  şey yazmıyor, yalnız kuyruğa alıyor.
* `whatsapp_pairing_codes`  — kod defteri; `whatsapp_pairing_attempts`ten
  AYRI ve KİRACI tablosudur (company_id + user_id taşır).

`whatsapp_pairing_attempts` taşındı ÇÜNKÜ kiracıya bağlı DEĞİLDİR: deneme
yapan numara henüz hiçbir firmaya ait değildir. Bu PR ona HİÇ YAZMAZ;
şemayla birlikte doğması, eşleştirme PR'ını ikinci bir göçten kurtarır.

CHECK kısıtları göçle (`20260910_0078`) BİREBİR aynıdır. Alembic ile
kurulan şema ile `metadata.create_all()` ile kurulan şema güvenlik anlamı
bakımından AYRIŞMAMALIDIR; ayrışsaydı testler gerçekte üretimde tutan bir
kısıtı hiç ölçmezdi. Bu MetaData uygulama açılışında `create_all`
EDİLMEZ (kapı: `tests/test_wa1_ingress.py::
test_ACILIS_DDLi_GOCUN_ONUNE_GECMIYOR`); tabloların TEK doğum yeri göçtür.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()


# --- gelen mesaj durum kataloğu (kapalı küme) ------------------------------
#: webhook yazdı, işçi henüz almadı — BU PR'DA YAZILAN TEK DEĞER.
RECEIVED = "RECEIVED"
#: bir işçi lease aldı (`locked_until` + `lock_token`).
PROCESSING = "PROCESSING"
#: cevap üretildi.
ANSWERED = "ANSWERED"
#: sessiz son: bilinmeyen numara, boş metin, desteklenmeyen tür.
IGNORED = "IGNORED"
#: kalıcı hata; yeniden DENENMEZ. Kaynakta bu değerin adı `FAILED`di; bu
#: depoda `DEAD` çünkü `FAILED` adı bildirim outbox'ında ZATEN başka bir
#: anlamda kullanılıyor (yeniden denenebilir başarısızlık) ve iki kuyruğun
#: aynı sözcüğü ters anlamda kullanması, süpürücüyü yazan kişinin
#: yapabileceği en sessiz hatadır.
DEAD = "DEAD"

#: Göçün `ck_whatsapp_inbound_status` CHECK'i ile BİREBİR aynı beş değer.
INBOUND_STATUSES = frozenset({RECEIVED, PROCESSING, ANSWERED, IGNORED, DEAD})


whatsapp_inbound = Table(
    "whatsapp_inbound",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # UNIQUE kısıt idempotency'nin KENDİSİDİR: aynı wamid ikinci kez INSERT
    # edilemez, dolayısıyla ikinci kez işlenemez. Hakem uygulama değil,
    # veritabanıdır — "önce SELECT sonra INSERT" yarışı yoktur.
    Column("wamid", String(128), nullable=False),
    # Kanonik numara (`telefon.normalize_phone`): artı işaretsiz rakamlar.
    Column("sender_phone", String(20), nullable=False),
    Column("phone_number_id", String(32), nullable=False),
    # NOT NULL; medya satırında BOŞ STRING (altyazı yoksa).
    Column("text", Text, nullable=False),
    # İkili veri DEĞİL, Meta'daki kimliği: indirme işçinin işidir.
    Column("media_id", String(128), nullable=True),
    Column("media_mime", String(64), nullable=True),
    Column("status", String(20), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("locked_until", DateTime(timezone=True), nullable=True),
    Column("lock_token", String(64), nullable=True),
    Column("last_error", Text, nullable=True),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("processed_at", DateTime(timezone=True), nullable=True),
    CheckConstraint(
        "status IN ('RECEIVED','PROCESSING','ANSWERED','IGNORED','DEAD')",
        name="ck_whatsapp_inbound_status",
    ),
    CheckConstraint("attempt_count >= 0", name="ck_whatsapp_inbound_attempt_count"),
    UniqueConstraint("wamid", name="uq_whatsapp_inbound_wamid"),
    Index("ix_whatsapp_inbound_status", "status"),
)


#: Deneme penceresi ve sınırları — kaynakla AYNI sayılar. Bu PR'da HİÇ
#: KULLANILMAZ; eşleştirme akışıyla birlikte yürürlüğe girecekler.
PAIRING_PENCERE_DAKIKA = 15
PAIRING_PENCERE_SINIRI = 5

whatsapp_pairing_attempts = Table(
    "whatsapp_pairing_attempts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # Kiracıya bağlı DEĞİL: deneme yapan numara henüz hiçbir firmaya ait
    # değildir (modül başlığı).
    Column("phone", String(20), nullable=False),
    Column("window_start", DateTime(timezone=True), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("attempt_count >= 0", name="ck_whatsapp_pairing_attempts_count"),
    # Aynı numara aynı pencerede TEK satır: tekil olmasaydı iki eşzamanlı
    # deneme iki satır üretir ve sınır HİÇ ısırmazdı.
    UniqueConstraint(
        "phone", "window_start", name="uq_whatsapp_pairing_attempts_pencere"
    ),
    Index("ix_whatsapp_pairing_attempts_pencere", "window_start"),
)


__all__ = [
    "ANSWERED",
    "DEAD",
    "IGNORED",
    "INBOUND_STATUSES",
    "PAIRING_PENCERE_DAKIKA",
    "PAIRING_PENCERE_SINIRI",
    "PROCESSING",
    "RECEIVED",
    "metadata",
    "whatsapp_inbound",
    "whatsapp_pairing_attempts",
]
