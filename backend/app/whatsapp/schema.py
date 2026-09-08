"""WA1 giriş tabloları (Core, ORM'siz) — İKİSİ DE PLATFORM TABLOSU.

Kaynak `nazgul_website/backend/app/whatsapp/schema.py`. Oradaki ALTI
tablodan bu depoya YALNIZ İKİSİ taşındı ve ikisi de DEĞİŞTİRİLEREK: kaynağın
`whatsapp_inbound`u `company_id`/`user_id` sütunları taşıyordu, bu depoda
TAŞIMIYOR. Gerekçe göçün başlığındadır (`20260910_0078`): webhook'a gelen
mesaj henüz hiçbir firmaya ait değildir ve bu depoda kiracı sınırı bir
NÖBETÇİYLE korunuyor — `company_id` taşıyan her tablo `TENANT_TABLES`a
girer ve her sorgusundan `company_id=:cid` yüklemi istenir. Karşılığı
olmayan bir yüklem, kuralı hem yalan hem işlevsiz yapardı.

WA2 (göç `20260910_0079`) bu listeye ÜÇ tablo daha ekledi ve üçü de
KİRACI tablosudur (`company_id` TAŞIR, yani `TENANT_TABLES`a GİRER):

* `whatsapp_links`          — "bu numara KİM?" Eşleştirme defteri.
* `whatsapp_pairing_codes`  — tek kullanımlık kod defteri; digest saklar,
  düz kod ASLA. `whatsapp_pairing_attempts`ten AYRIDIR: o platform sayacı,
  bu kiracı defteri.
* `whatsapp_context`        — "hangi firmadasın?" `FİRMA SEÇ` komutunun
  yazdığı aktif firma seçimi.

TAŞINMAYAN TEK TABLO ve gerekçesi (bu dilimin KAPSAMI DIŞINDA):

* `whatsapp_pending_actions`— iki adımlı yazma taslakları; bu PR hiçbir
  finansal yazma yapmıyor.

`whatsapp_pairing_attempts` WA1'de taşındı ÇÜNKÜ kiracıya bağlı DEĞİLDİR: deneme
yapan numara henüz hiçbir firmaya ait değildir. WA1 ona HİÇ YAZMIYORDU; WA2
(`eslestirme.deneme_say`) yazan İLK ve TEK çağırandır — yani şemayla
birlikte doğması eşleştirmeyi ikinci bir göçten gerçekten kurtardı.

CHECK kısıtları göçlerle (`20260910_0078`, `20260910_0079`) BİREBİR
aynıdır. Alembic ile
kurulan şema ile `metadata.create_all()` ile kurulan şema güvenlik anlamı
bakımından AYRIŞMAMALIDIR; ayrışsaydı testler gerçekte üretimde tutan bir
kısıtı hiç ölçmezdi. Bu MetaData uygulama açılışında `create_all`
EDİLMEZ (kapı: `tests/test_wa1_ingress.py::
test_ACILIS_DDLi_GOCUN_ONUNE_GECMIYOR`); tabloların TEK doğum yeri göçtür.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
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

# --- WA3 işçi kirası (lease) — GÖÇ GEREKTİRMEZ ----------------------------
# İkisi de WA1'in AÇTIĞI sütunların (`locked_until`, `attempt_count`)
# POLİTİKASIDIR, şeması değil: değerleri değiştirmek hiçbir DDL gerektirmez.

#: Bir işçi satırı ne kadar süreyle KİRALAR. Süre dolunca başka bir işçi
#: devralabilir. BEŞ DAKİKA: sağlayıcı zaman aşımı 15 saniye
#: (`saglayici.ZAMAN_ASIMI_SANIYE`), yani beş dakika normal bir turun
#: ONLARCA katıdır ve yalnız GERÇEKTEN ölmüş bir işçi bu süreyi aşar.
#: Daha kısası, yavaş ama yaşayan bir işçinin işini ikinci kez yaptırırdı
#: (Meta'ya YİNELENEN cevap).
LEASE_DAKIKA = 5

#: Bir satır en çok kaç kez CLAIM edilebilir. Sayaç claim'de artar (geçici
#: hatada satır RECEIVED'a döner ve yeniden denenir); tavana ulaşan satır
#: bir daha claim EDİLEMEZ ve `takilanlari_kapat` onu DEAD yapar.
#:
#: ÜÇ, BEŞ DEĞİL: her deneme Meta'ya bir gönderim denemesi demektir ve
#: kalıcı bir sağlayıcı arızasında üç deneme zaten "geçici değilmiş"
#: sonucunu verir. Tavan geçildiğinde satır SESSİZCE kaybolmaz — DEAD
#: damgası ve `last_error` kuyrukta durur.
MAX_DENEME = 3


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
#: Cevap ÜRETİLEN deneme sayısı (WA2'de yürürlüğe girdi). Sınırın ALTINDA
#: ama bunun ÜSTÜNDE olan deneme İŞLENİR, ama cevap VERİLMEZ: kullanıcı ilk
#: birkaç denemede gerçek bir hata yapmış olabilir; ötesinde sessiz düşürme,
#: Meta mesaj maliyeti üzerinden kurulacak bir masraf saldırısını kapatır.
PAIRING_CEVAP_SINIRI = 3

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



# ---------------------------------------------------------------------------
# WA2 — EŞLEŞTİRME DEFTERİ (göç 20260910_0079). ÜÇÜ DE KİRACI TABLOSU.
# ---------------------------------------------------------------------------
# Bütün müşteriler AYNI bot numarasına yazar; "bu numara kim?" sorusunun
# cevabı `whatsapp_links`tir. `whatsapp_pairing_codes` o satırın GÜVENLİ
# biçimde nasıl doğduğunu, `whatsapp_context` ise numara birden çok firmaya
# bağlıysa hangisinin AKTİF olduğunu tanımlar.

whatsapp_links = Table(
    "whatsapp_links",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, nullable=False),
    # FK'ler göçtedir (`companies.id`, `app_users.id` ON DELETE CASCADE).
    # Buradaki Core tanımı ayrı bir MetaData'da yaşadığı için FK nesnesi
    # çözülemez; `notifications/schema.py` ile AYNI gelenek.
    Column("user_id", Integer, nullable=False),
    # Kanonik numara (`telefon.normalize_phone`): artı işaretsiz rakamlar.
    # `whatsapp_inbound.sender_phone` ile AYNI genişlik ve AYNI biçim — iki
    # taraf aynı değeri karşılaştırmak zorunda.
    Column("phone", String(20), nullable=False),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("created_by", Integer, nullable=True),
    Index("ix_whatsapp_links_phone", "phone"),
)

# AKTİF NUMARA TEKİLLİĞİ — `(company_id, phone)`, KAYNAKTAN AYRILAN TEK YER.
#
# Kaynak (`nazgul_website`) bu indeksi KÜRESEL yazıyor (`company_id` anahtarda
# YOK) çünkü orada firma seçici YOKTU. Bu depoda seçici VAR (`whatsapp_context`
# + `FİRMA SEÇ`), bu yüzden aynı numara İKİ FARKLI firmada aktif olabilir ve
# olmalıdır: muhasebecisi iki firmaya bakan kullanıcı bu ürünün tipik
# kullanıcısıdır. Gerekçenin TAMAMI göç `20260910_0079`un başlığında.
#
# KISMİ (WHERE'li), DÜZ DEĞİL: `UNIQUE(company_id, phone, is_active)` yazsaydık
# pasif satırlar da anahtara girer ve aynı numara aynı firmada İKİ KEZ
# kapatılamazdı. Tablo tanımının DIŞINDA, çünkü WHERE ifadesi gerçek kolon
# nesnesine bağlanmak zorunda. SQLite ve PostgreSQL'in İKİSİ de destekliyor.
Index(
    "uq_whatsapp_links_aktif_numara",
    whatsapp_links.c.company_id,
    whatsapp_links.c.phone,
    unique=True,
    sqlite_where=whatsapp_links.c.is_active.is_(True),
    postgresql_where=whatsapp_links.c.is_active.is_(True),
)


# --- kod defteri: durum makinesi (tek yön, geri dönüş YOK) -----------------
#   PENDING → CONSUMED    (kod kullanıldı; bağlantı AYNI transaction'da doğdu)
#   PENDING → CANCELLED   (yönetici iptali ya da yeni kod üretimi)
#   PENDING → EXPIRED     (süre doldu; süpürücü damgalar)
PAIRING_PENDING = "PENDING"
PAIRING_CONSUMED = "CONSUMED"
PAIRING_CANCELLED = "CANCELLED"
PAIRING_EXPIRED = "EXPIRED"

#: Göçün `ck_wpc_status` CHECK'i ile BİREBİR aynı dört değer.
PAIRING_STATUSES = frozenset(
    {PAIRING_PENDING, PAIRING_CONSUMED, PAIRING_CANCELLED, PAIRING_EXPIRED}
)

#: Kod ömrü: kullanıcının paneli görüp WhatsApp'a geçmesine yeter, çalınan
#: bir ekran görüntüsünün değerini uzun süre korumasına yetmez.
PAIRING_OMRU_DAKIKA = 10

#: Kod SATIRI başına yanlış deneme tavanı. Kod BULUNDUĞUNDA çalışır; hiç var
#: olmayan kod denemelerinin sınırı `whatsapp_pairing_attempts`tedir.
PAIRING_MAX_ATTEMPTS = 5

whatsapp_pairing_codes = Table(
    "whatsapp_pairing_codes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, nullable=False),
    Column("user_id", Integer, nullable=False),
    Column("created_by", Integer, nullable=True),
    # DÜZ KOD ASLA SAKLANMAZ: yalnız SHA-256 hex özeti (`auth.token_digest`
    # ile AYNI sözleşme). Özet `WHATSAPP_APP_SECRET`e BAĞLANMAZ — App Secret
    # rotasyonu bekleyen kodları geçersiz kılmamalıdır.
    Column("code_digest", String(64), nullable=False),
    Column("status", String(12), nullable=False, default=PAIRING_PENDING),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("attempt_count", Integer, nullable=False, default=0),
    Column("max_attempts", Integer, nullable=False, default=PAIRING_MAX_ATTEMPTS),
    Column("consumed_at", DateTime(timezone=True), nullable=True),
    Column("cancelled_at", DateTime(timezone=True), nullable=True),
    Column("consumed_link_id", Integer, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("code_digest", name="uq_wpc_code_digest"),
    Index("ix_wpc_company_status", "company_id", "status", "expires_at"),
    # YEDİ CHECK — göç `20260910_0079` ile BİREBİR aynı. Alembic ile kurulan
    # şema ile `metadata.create_all()` ile kurulan şema güvenlik anlamı
    # bakımından AYRIŞMAMALIDIR; ayrışsaydı testler gerçekte üretimde tutan
    # bir kısıtı hiç ölçmezdi.
    #
    # DURUM x ZAMAN DAMGASI MATRİSİ — her durum için TAM tanım:
    #   PENDING  : üçü de NULL
    #   CONSUMED : consumed_at + consumed_link_id dolu, cancelled_at NULL
    #   CANCELLED: cancelled_at dolu, consumed_* NULL
    #   EXPIRED  : üçü de NULL
    CheckConstraint(
        "status IN ('PENDING','CONSUMED','CANCELLED','EXPIRED')",
        name="ck_wpc_status",
    ),
    CheckConstraint("attempt_count >= 0", name="ck_wpc_attempt_count"),
    CheckConstraint("max_attempts > 0", name="ck_wpc_max_attempts"),
    CheckConstraint(
        "(status <> 'PENDING') OR "
        "(consumed_at IS NULL AND cancelled_at IS NULL AND consumed_link_id IS NULL)",
        name="ck_wpc_pending_temiz",
    ),
    CheckConstraint(
        "(status <> 'CONSUMED') OR "
        "(consumed_at IS NOT NULL AND consumed_link_id IS NOT NULL "
        "AND cancelled_at IS NULL)",
        name="ck_wpc_consumed_alanlari",
    ),
    CheckConstraint(
        "(status <> 'CANCELLED') OR "
        "(cancelled_at IS NOT NULL AND consumed_at IS NULL "
        "AND consumed_link_id IS NULL)",
        name="ck_wpc_cancelled_alani",
    ),
    CheckConstraint(
        "(status <> 'EXPIRED') OR "
        "(consumed_at IS NULL AND consumed_link_id IS NULL "
        "AND cancelled_at IS NULL)",
        name="ck_wpc_expired_temiz",
    ),
)

# Firma+kullanıcı başına EN FAZLA BİR bekleyen kod. "Yeni kod eskisini iptal
# eder" kuralının hakemi uygulama sorgusu DEĞİL, bu indekstir: iki yönetici
# aynı anda kod üretse bile tek bekleyen kod kalır.
Index(
    "uq_wpc_aktif_kod",
    whatsapp_pairing_codes.c.company_id,
    whatsapp_pairing_codes.c.user_id,
    unique=True,
    sqlite_where=whatsapp_pairing_codes.c.status == PAIRING_PENDING,
    postgresql_where=whatsapp_pairing_codes.c.status == PAIRING_PENDING,
)


#: Bağlam ömrü. KISA tutuldu ve gerekçesi güvenliktir: "aktif firma" bir
#: YETKİ SEÇİMİDİR ve seçim, kullanıcının o an ne yaptığını bildiği ANA
#: bağlıdır. Bir gün sonra gelen "borcum ne kadar" mesajının, dün seçilmiş
#: firmaya sessizce cevap vermesi YANLIŞ firmanın rakamını verirdi.
BAGLAM_OMRU_DAKIKA = 30

whatsapp_context = Table(
    "whatsapp_context",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, nullable=False),
    Column("user_id", Integer, nullable=False),
    Column("phone", String(20), nullable=False),
    # JSON: {"aktif_firma": <company_id>}. YALNIZ uygulama yazar/okur ve
    # içinden hiçbir YETKİ iddiası okunmaz — `aktif_firma` her okumada
    # BAĞLANTI DEFTERİNE karşı yeniden doğrulanır (`baglam.aktif_firma_coz`).
    Column("payload", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    # Üçlü başına TEK bağlam: yenisi eskisini değiştirir.
    UniqueConstraint(
        "company_id", "user_id", "phone", name="uq_whatsapp_context_scope"
    ),
)


__all__ = [
    "ANSWERED",
    "BAGLAM_OMRU_DAKIKA",
    "DEAD",
    "IGNORED",
    "LEASE_DAKIKA",
    "MAX_DENEME",
    "INBOUND_STATUSES",
    "PAIRING_CANCELLED",
    "PAIRING_CEVAP_SINIRI",
    "PAIRING_CONSUMED",
    "PAIRING_EXPIRED",
    "PAIRING_MAX_ATTEMPTS",
    "PAIRING_OMRU_DAKIKA",
    "PAIRING_PENCERE_DAKIKA",
    "PAIRING_PENCERE_SINIRI",
    "PAIRING_PENDING",
    "PAIRING_STATUSES",
    "PROCESSING",
    "RECEIVED",
    "metadata",
    "whatsapp_context",
    "whatsapp_inbound",
    "whatsapp_links",
    "whatsapp_pairing_attempts",
    "whatsapp_pairing_codes",
]
