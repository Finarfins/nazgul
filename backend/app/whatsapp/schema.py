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

WA4 (göç `20260910_0080`) o listeye SON tabloyu ekledi ve o da KİRACI
tablosudur:

* `whatsapp_pending_actions` — iki adımlı yazma taslakları. WA2'nin
  başlığında bu tablo "bu dilimin KAPSAMI DIŞINDA" diye anılıyordu ve
  gerekçesi "bu PR hiçbir finansal yazma yapmıyor"du. WA4 finansal yazmayı
  GETİRDİĞİ için tablo da onunla birlikte doğdu: bir sohbet mesajı ASLA
  doğrudan para kaydetmez — önce taslak, sonra kullanıcının açık `ONAY`ı.

Böylece kaynaktaki ALTI tablonun ALTISI da bu depoda; taşıma TAMAMLANDI.

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


#: F10-1b — ÇİFTÇİ MESAJ SAYACI (göç `20260920_0091`). Pencere ve sınırlar
#: `PAIRING_*` ile AYNI büyüklük sınıfındadır (keşif §5.3, K6 kararı).
#:
#: SINIR YALNIZ ÇİFTÇİ YOLUNA UYGULANIR. Personel yolunda bugün numara
#: başına sınır YOKTUR (keşif §1.5 ölçtü) ve bu PR onu DEĞİŞTİRMEZ:
#: personel içeriden gelen, sayısı az ve kimliği daha dar bir zincirden
#: geçmiş bir taraftır; çiftçi dış taraftır, sayısı çoktur ve her cevap
#: Meta'ya ücretli bir mesajdır.
MESAJ_PENCERE_DAKIKA = 15
MESAJ_PENCERE_SINIRI = 20
#: Cevap ÜRETİLEN mesaj sayısı. Sınırın ALTINDA ama bunun ÜSTÜNDE olan
#: mesaj İŞLENİR (yani `DUR` hâlâ ısırır, rıza yazması hâlâ düşer) ama
#: cevap VERİLMEZ — `PAIRING_CEVAP_SINIRI`nin gerekçesi (bu dosyada, 15
#: satır yukarıda) burada AYNEN geçerlidir: ilk mesajlarda kullanıcı
#: gerçek bir hata yapmış olabilir, ötesinde sessiz düşürme Meta mesaj
#: maliyeti üzerinden kurulacak bir masraf saldırısını kapatır.
MESAJ_CEVAP_SINIRI = 15

whatsapp_message_attempts = Table(
    "whatsapp_message_attempts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # KİRACIYA BAĞLI DEĞİL — `whatsapp_pairing_attempts` ile AYNI gerekçe ve
    # burada bir adım daha güçlüsü: sınırın KORUDUĞU şey firmanın verisi
    # değil, BOT NUMARASININ mesaj bütçesidir. `company_id` taşısaydı iki
    # firmaya birden bağlı bir çiftçi (aynı numara, iki alım merkezi) sınırı
    # İKİYE KATLARDI — kapı `test_SAYAC_NUMARA_BASINA_FIRMALAR_ARASI`.
    Column("phone", String(20), nullable=False),
    Column("window_start", DateTime(timezone=True), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("attempt_count >= 0", name="ck_whatsapp_message_attempts_count"),
    # AYRI TABLO, `whatsapp_pairing_attempts`e `kind` SÜTUNU DEĞİL (K6):
    # mevcut tablonun anahtarı `(phone, window_start)`tır ve `kind` eklemek
    # o anahtarı DEĞİŞTİRMEK, yani yürürlükteki eşleştirme sınırını bir göç
    # boyunca zayıflatmak olurdu.
    UniqueConstraint(
        "phone", "window_start", name="uq_whatsapp_message_attempts_pencere"
    ),
    Index("ix_whatsapp_message_attempts_pencere", "window_start"),
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
    # KOD KIME VERILDI (goc `20260912_0082`, SEC-1). `whatsapp_links.phone`
    # ile AYNI genislik ve AYNI bicim (`telefon.normalize_phone` ciktisi):
    # `kod_kullan` iki degeri KARSILASTIRIYOR ve bicim ayrisirsa hicbir kod
    # eslesmez. `server_default=""` GOCTEKIYLE BIREBIR: bos dize hicbir
    # cagiranla eslesemez (FAIL-CLOSED), gerekce gocun basliginda.
    #
    # FIZIKSEL SIRA FARKLI ve bu onemsiz: sutun tabloya 0082 ile EKLENDI,
    # yani gercek semada EN SONDA duruyor. Denetleyiciler (`inspect`) sutunu
    # ADIYLA okuyor; her iki kapinin da olctugu sey ad, tip ve nullable'dir.
    Column(
        "target_phone",
        String(20),
        nullable=False,
        server_default="",
    ),
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


# ---------------------------------------------------------------------------
# WA4 - BEKLEYEN ISLEM DEFTERI (goc 20260910_0080). KIRACI TABLOSU.
# ---------------------------------------------------------------------------
# Iki adimli yazmanin tasiyicisi: niyet cozulur (`niyet.tahsilat_coz`),
# TASLAK acilir ve kullaniciya ozet gosterilir, yalniz acik `ONAY` uzerine
# `payment_allocation_engine` cagrilir. Gerekcenin tamami goc
# `20260910_0080`in basligindadir.

#: Kapali kume - gocun `ck_wpa_action_type` CHECK'i ile BIREBIR ayni.
#: BUGUN TEK UYE: tahsilat. Kumeyi buyutmek, buyuyen her uyenin kendi
#: uygulama yolunu ve kendi idempotency anahtarini getirmesini gerektirir.
TAHSILAT = "TAHSILAT"
ISLEM_TURLERI: frozenset[str] = frozenset({TAHSILAT})

# --- bekleyen islem durum makinesi -----------------------------------------
#   PENDING  -> APPLYING  (ONAY; tek UPDATE'lik CAS)
#   PENDING  -> CANCELLED (IPTAL)
#   PENDING  -> EXPIRED   (supurucu ya da okuma anindaki tembel kapatma)
#   APPLYING -> APPLIED   (odeme yazildi; `result_id` dolu)
#   APPLYING -> FAILED    (kalici hata; `fail_reason` SINIF adi)
#   APPLYING -> PENDING   (gecici hata; taslak omru icinde yeniden denenir)
#
#: ADLAR `BEKLEYEN_` ONEKLI ve bu bilincli. Onek olmasaydi `FAILED` adi bu
#: modulde bildirim outbox'inin `FAILED`i (YENIDEN DENENEBILIR basarisizlik)
#: ile ayni sozcugu TERS anlamda kullanirdi - WA1'in `DEAD` kararinin
#: gerekcesiyle BIREBIR ayni sebep. Burada `FAILED` KALICIDIR.
BEKLEYEN_PENDING = "PENDING"
BEKLEYEN_APPLYING = "APPLYING"
BEKLEYEN_APPLIED = "APPLIED"
BEKLEYEN_CANCELLED = "CANCELLED"
BEKLEYEN_EXPIRED = "EXPIRED"
BEKLEYEN_FAILED = "FAILED"

#: Gocun `ck_wpa_status` CHECK'i ile BIREBIR ayni alti deger.
BEKLEYEN_STATUSES: frozenset[str] = frozenset({
    BEKLEYEN_PENDING, BEKLEYEN_APPLYING, BEKLEYEN_APPLIED,
    BEKLEYEN_CANCELLED, BEKLEYEN_EXPIRED, BEKLEYEN_FAILED,
})

#: Kismi UNIQUE indeksin kapsami - gocun `_aktif_yuklem()`i ile BIREBIR ayni
#: iki deger. Bu ikisi "aktif"tir: kapsamda ayni anda EN FAZLA BIRI olabilir.
BEKLEYEN_AKTIF_STATUSES: frozenset[str] = frozenset({
    BEKLEYEN_PENDING, BEKLEYEN_APPLYING,
})

#: Terminal durumlar: bir daha kimildamazlar.
BEKLEYEN_TERMINAL_STATUSES: frozenset[str] = frozenset({
    BEKLEYEN_APPLIED, BEKLEYEN_CANCELLED, BEKLEYEN_EXPIRED, BEKLEYEN_FAILED,
})

#: Taslak omru. Kullanicinin ozeti okuyup `ONAY` yazmasina fazlasiyla yeter;
#: yarin gelen bir `ONAY`in dunku tutari yazmasina yetmez. `BAGLAM_OMRU_
#: DAKIKA` (30) ile AYNI buyukluk sinifinda olmasi tesaduf degil: taslak,
#: baglamin omrunden uzun yasarsa hangi firmaya yazilacagi belirsizlesirdi.
PENDING_OMRU_DAKIKA = 15

#: Lease suresi: bir iscinin uygulamayi bitirmesi icin makul ust sinir.
#: Bu sure doldugunda APPLYING satir DEVRALINABILIR (`bekleyen.claim_et`) -
#: guvenli, cunku ikinci deneme AYNI `islem_anahtari` ile gider ve odeme
#: defteri ikinci bir odeme YAZMAZ.
PENDING_LEASE_DAKIKA = 5

whatsapp_pending_actions = Table(
    "whatsapp_pending_actions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, nullable=False),
    # FK'ler goctedir. `(company_id, whatsapp_link_id)` BILESIK olarak
    # `whatsapp_links(company_id, id)`ye baglanir: bir firmanin taslagi
    # BASKA firmanin baglantisina asili gorunemez.
    Column("user_id", Integer, nullable=False),
    Column("whatsapp_link_id", Integer, nullable=False),
    Column("phone", String(20), nullable=False),
    Column("action_type", String(30), nullable=False),
    # Para degerleri METIN olarak yazilir (`bekleyen._yuk_denetle` float
    # yuku REDDEDER); JSON sayisina donen bir tutar ikili kayan nokta olurdu.
    Column("payload", Text, nullable=False),
    Column("status", String(12), nullable=False),
    Column("islem_anahtari", String(64), nullable=False),
    Column("claim_token", String(64), nullable=True),
    Column("claim_expires_at", DateTime(timezone=True), nullable=True),
    Column("result_id", Integer, nullable=True),
    Column("fail_reason", String(64), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("resolved_at", DateTime(timezone=True), nullable=True),
    # DORT CHECK - goc `20260910_0080` ile BIREBIR ayni. Alembic ile kurulan
    # sema ile `metadata.create_all()` ile kurulan sema guvenlik anlami
    # bakimindan AYRISMAMALIDIR; ayrissaydi testler gerceklte uretimde tutan
    # bir kisiti hic olcmezdi.
    CheckConstraint(
        "action_type IN ('TAHSILAT')", name="ck_wpa_action_type"
    ),
    CheckConstraint(
        "status IN ('PENDING','APPLYING','APPLIED','CANCELLED','EXPIRED','FAILED')",
        name="ck_wpa_status",
    ),
    CheckConstraint(
        "(status <> 'APPLIED') OR (result_id IS NOT NULL)",
        name="ck_wpa_applied_result",
    ),
    CheckConstraint(
        "(status IN ('PENDING','APPLYING')) OR "
        "(claim_token IS NULL AND claim_expires_at IS NULL)",
        name="ck_wpa_terminal_lease_temiz",
    ),
    # Odeme idempotensisinin koku - gerekce gocun basliginda. KURESEL tekil.
    UniqueConstraint("islem_anahtari", name="uq_wpa_islem_anahtari"),
    Index("ix_wpa_status_expires", "status", "expires_at"),
)

# KAPSAMDA TEK AKTIF TASLAK - `(company_id, user_id, phone)` WHERE aktif.
#
# Hakem uygulama sorgusu DEGIL bu indekstir: iki mesaj ayni anda gelse bile
# ikinci taslak IntegrityError'a carpar. Kural olmasaydi kullanicinin
# yazdigi `ONAY` kelimesinin HANGI taslaga ait oldugu belirsiz kalirdi -
# sohbette bir secim arayuzu yoktur.
#
# KISMI (WHERE'li), DUZ DEGIL: terminal satirlar anahtarin DISINDA kalir,
# yani ayni kullanici ayni numaradan IKINCI KEZ tahsilat yapabilir.
# Tablo tanimin DISINDA, cunku WHERE ifadesi gercek kolon nesnesine
# baglanmak zorunda. SQLite ve PostgreSQL'in IKISI de destekliyor.
Index(
    "uq_wpa_aktif_taslak",
    whatsapp_pending_actions.c.company_id,
    whatsapp_pending_actions.c.user_id,
    whatsapp_pending_actions.c.phone,
    unique=True,
    sqlite_where=whatsapp_pending_actions.c.status.in_(
        sorted(BEKLEYEN_AKTIF_STATUSES)
    ),
    postgresql_where=whatsapp_pending_actions.c.status.in_(
        sorted(BEKLEYEN_AKTIF_STATUSES)
    ),
)


# ---------------------------------------------------------------------------
# F10-1a — TARAF BAGLANTI DEFTERI (goc 20260918_0090). IKISI DE KIRACI TABLOSU.
# ---------------------------------------------------------------------------
# `whatsapp_links` "bu numara hangi PERSONEL" sorusunu cevapliyor; bu iki
# tablo "bu numara hangi CARI" sorusunu cevapliyor. Ciftci bir `app_users`
# satiri DEGILDIR ve tek bir cari bile degildir: ekstre tarafi `customers`,
# avans/makbuz tarafi `suppliers` (kesif §3.1). Bu yuzden bag POLIMORFIKTIR
# ve sozlugu `notifications/consents.py::PARTY_TYPES` ile BIREBIR aynidir.
#
# CHECK kisitlari goc `20260918_0090` ile BIREBIR aynidir — bu dosyanin
# basligindaki ev kurali: Alembic ile kurulan sema ile `metadata.create_all()`
# ile kurulan sema guvenlik anlami bakimindan AYRISMAMALIDIR.

#: Gocun `ck_wpl_party_type` / `ck_wppc_party_type` CHECK'i ile BIREBIR ayni
#: iki deger. `consents.PARTY_TYPES` ile de birebir; ayri yazildi cunku bu
#: modul `notifications` paketini CEKMEZ (cevrimsiz import garantisi) ve bir
#: import, sozlugun sessizce ayrilmasini ZATEN engellemezdi — kapi
#: `tests/test_f10_1a_taraf_baglantisi.py`de, iki kumeyi ESITLIYOR.
TARAF_CUSTOMER = "CUSTOMER"
TARAF_SUPPLIER = "SUPPLIER"
TARAF_TIPLERI: frozenset[str] = frozenset({TARAF_CUSTOMER, TARAF_SUPPLIER})

whatsapp_party_links = Table(
    "whatsapp_party_links",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, nullable=False),
    Column("party_type", String(12), nullable=False),
    # POLIMORFIK: gercek FK YOK. `notification_consents.party_id` ile AYNI
    # durum ve AYNI cozum (goc basligi).
    Column("party_id", Integer, nullable=False),
    # Kanonik numara (`telefon.normalize_phone`), `whatsapp_links.phone` ile
    # AYNI genislik ve bicim.
    Column("phone", String(20), nullable=False),
    Column("is_active", Boolean, nullable=False, default=True),
    # Riza IZI, karar DEGIL: karar her cevapta `notification_consents`ten
    # yeniden okunur (consents.py sozlesme 2). F10-1a'da HER ZAMAN NULL'dur —
    # rizayi personelin urettigi kod DEGIL, ciftcinin ilk mesaji verir
    # (F10-1b) ve damga o an dolar. NULL = "bu baglanti henuz riza tasimiyor".
    Column("consent_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("created_by", Integer, nullable=True),
    CheckConstraint(
        "party_type IN ('CUSTOMER','SUPPLIER')", name="ck_wpl_party_type"
    ),
    CheckConstraint("party_id > 0", name="ck_wpl_party_id"),
    UniqueConstraint("company_id", "id", name="uq_whatsapp_party_links_company_id"),
    Index("ix_whatsapp_party_links_phone", "phone"),
    Index("ix_whatsapp_party_links_taraf", "company_id", "party_type", "party_id"),
)

# AKTIF NUMARA TEKILLIGI — `whatsapp_links`in kuralinin BIREBIR aynisi ve ayni
# gerekce: bir numara BIR FIRMADA en fazla bir aktif taraf baglantisi tasir,
# BASKA firmada tasiyabilir (bir ciftci iki alim merkezine urun verir).
# KISMI (WHERE'li): pasif satirlar anahtarin DISINDA kalir.
Index(
    "uq_whatsapp_party_links_aktif_numara",
    whatsapp_party_links.c.company_id,
    whatsapp_party_links.c.phone,
    unique=True,
    sqlite_where=whatsapp_party_links.c.is_active.is_(True),
    postgresql_where=whatsapp_party_links.c.is_active.is_(True),
)


whatsapp_party_pairing_codes = Table(
    "whatsapp_party_pairing_codes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, nullable=False),
    Column("party_type", String(12), nullable=False),
    Column("party_id", Integer, nullable=False),
    # KOD BIR NUMARAYA VERILIR (SEC-1, goc `20260912_0082`nin kurali). Burada
    # `server_default` YOK ve bu 0082'den BILINCLI bir ayrilik: o varsayilan
    # var olan satirlari doldurmak icindi; bu tablo BOS doguyor ve bos hedef
    # bir sozlesme ihlalidir — `ck_wppc_target_phone` onu REDDEDER.
    Column("target_phone", String(20), nullable=False),
    Column("created_by", Integer, nullable=True),
    Column("code_digest", String(64), nullable=False),
    Column("status", String(12), nullable=False, default=PAIRING_PENDING),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("attempt_count", Integer, nullable=False, default=0),
    Column("max_attempts", Integer, nullable=False, default=PAIRING_MAX_ATTEMPTS),
    Column("consumed_at", DateTime(timezone=True), nullable=True),
    Column("cancelled_at", DateTime(timezone=True), nullable=True),
    Column("consumed_link_id", Integer, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("code_digest", name="uq_wppc_code_digest"),
    UniqueConstraint("company_id", "id", name="uq_wppc_company_id"),
    Index("ix_wppc_company_status", "company_id", "status", "expires_at"),
    CheckConstraint(
        "party_type IN ('CUSTOMER','SUPPLIER')", name="ck_wppc_party_type"
    ),
    CheckConstraint("target_phone <> ''", name="ck_wppc_target_phone"),
    # YEDI CHECK — goc `20260918_0090` ile BIREBIR, 0079'un matrisi:
    #   PENDING  : ucu de NULL
    #   CONSUMED : consumed_at + consumed_link_id dolu, cancelled_at NULL
    #   CANCELLED: cancelled_at dolu, consumed_* NULL
    #   EXPIRED  : ucu de NULL
    CheckConstraint(
        "status IN ('PENDING','CONSUMED','CANCELLED','EXPIRED')",
        name="ck_wppc_status",
    ),
    CheckConstraint("attempt_count >= 0", name="ck_wppc_attempt_count"),
    CheckConstraint("max_attempts > 0", name="ck_wppc_max_attempts"),
    CheckConstraint(
        "(status <> 'PENDING') OR "
        "(consumed_at IS NULL AND cancelled_at IS NULL AND consumed_link_id IS NULL)",
        name="ck_wppc_pending_temiz",
    ),
    CheckConstraint(
        "(status <> 'CONSUMED') OR "
        "(consumed_at IS NOT NULL AND consumed_link_id IS NOT NULL "
        "AND cancelled_at IS NULL)",
        name="ck_wppc_consumed_alanlari",
    ),
    CheckConstraint(
        "(status <> 'CANCELLED') OR "
        "(cancelled_at IS NOT NULL AND consumed_at IS NULL "
        "AND consumed_link_id IS NULL)",
        name="ck_wppc_cancelled_alani",
    ),
    CheckConstraint(
        "(status <> 'EXPIRED') OR "
        "(consumed_at IS NULL AND consumed_link_id IS NULL "
        "AND cancelled_at IS NULL)",
        name="ck_wppc_expired_temiz",
    ),
)

# TARAF BASINA EN FAZLA BIR BEKLEYEN KOD — `uq_wpc_aktif_kod`un taraf ucluyle
# yeniden yazilmis hali. Hakem uygulama sorgusu DEGIL bu indekstir: iki
# personel ayni anda kod uretse bile tek bekleyen kod kalir.
Index(
    "uq_wppc_aktif_kod",
    whatsapp_party_pairing_codes.c.company_id,
    whatsapp_party_pairing_codes.c.party_type,
    whatsapp_party_pairing_codes.c.party_id,
    unique=True,
    sqlite_where=whatsapp_party_pairing_codes.c.status == PAIRING_PENDING,
    postgresql_where=whatsapp_party_pairing_codes.c.status == PAIRING_PENDING,
)


__all__ = [
    "ANSWERED",
    "BAGLAM_OMRU_DAKIKA",
    "BEKLEYEN_AKTIF_STATUSES",
    "BEKLEYEN_APPLIED",
    "BEKLEYEN_APPLYING",
    "BEKLEYEN_CANCELLED",
    "BEKLEYEN_EXPIRED",
    "BEKLEYEN_FAILED",
    "BEKLEYEN_PENDING",
    "BEKLEYEN_STATUSES",
    "BEKLEYEN_TERMINAL_STATUSES",
    "DEAD",
    "IGNORED",
    "LEASE_DAKIKA",
    "MAX_DENEME",
    "MESAJ_CEVAP_SINIRI",
    "MESAJ_PENCERE_DAKIKA",
    "MESAJ_PENCERE_SINIRI",
    "INBOUND_STATUSES",
    "ISLEM_TURLERI",
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
    "PENDING_LEASE_DAKIKA",
    "PENDING_OMRU_DAKIKA",
    "PROCESSING",
    "RECEIVED",
    "TAHSILAT",
    "TARAF_CUSTOMER",
    "TARAF_SUPPLIER",
    "TARAF_TIPLERI",
    "metadata",
    "whatsapp_context",
    "whatsapp_inbound",
    "whatsapp_links",
    "whatsapp_message_attempts",
    "whatsapp_pairing_attempts",
    "whatsapp_pairing_codes",
    "whatsapp_party_links",
    "whatsapp_party_pairing_codes",
    "whatsapp_pending_actions",
]
