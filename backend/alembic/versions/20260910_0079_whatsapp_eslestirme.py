"""WHATSAPP ESLESTIRME: numara -> ERP kimligi defteri (WA2).

Konu: bu goc SEMAYI kurar. Servis `app/whatsapp/eslestirme.py` ve
`app/whatsapp/baglam.py`de, uclar `app/routers/whatsapp.py`dedir.

--- OLCULEN EKSIK: KUYRUK VAR, KIMLIK YOK -------------------------------

WA1 (`20260910_0078`) Meta webhook'unun yazdigi PLATFORM kuyrugunu acti ve
basliginda bunu ACIKCA soyluyor: "numara hicbir kullaniciya BAGLANMAZ".
Yani depoda `whatsapp_inbound.sender_phone`daki rakam dizisini bir firmaya
ve bir kullaniciya ceviren HICBIR SEY yoktu — olculdu: `alembic/versions/`
altinda `whatsapp_links` adinda bir tablo YOKTU ve `app/whatsapp/` altinda
kimlik cozen tek bir fonksiyon bile YOKTU.

Bu goc o cevirinin KALICI DEFTERINI aciyor.

--- UC TABLO DA KIRACI TABLOSUDUR: `company_id` VAR ---------------------

Bu, bu gocun WA1'inkine TERS ama AYNI gerekceye dayanan kararidir.

WA1'in iki tablosu `company_id` TASIMIYOR cunku webhook'a gelen mesaj
henuz hicbir firmaya ait DEGILDIR. Bu gocun uc tablosu ise tam olarak "bu
numara HANGI firmanin hangi kullanicisi" sorusunun cevabidir; kiraci sutunu
DUSSEYDI ya sorunun cevabi olmazdi ya da cevap kuresel olurdu.

Uc tablo da `TENANT_TABLES` envanterine GIRER (envanter goc edilmis
semadan `company_id` sutunu tarayarak OTOMATIK turuyor) ve her sorgusundan
`company_id=:cid` yuklemi istenir. Karsiligi VARDIR: dort ucun dordu de
`request.state.company_id` ile daraliyor.

--- BILESIK YABANCI ANAHTAR: 0062'NIN KURALI ---------------------------

Uc tabloda da `UNIQUE(company_id, id)` var. Tek basina hicbir sorguyu
hizlandirmaz; BILESIK yabanci anahtarin HEDEFI olabilmek icin var.
`whatsapp_pairing_codes.consumed_link_id` bu sayede
`(company_id, consumed_link_id) -> whatsapp_links(company_id, id)` olarak
baglaniyor: bir firmanin kodu BASKA firmanin baglantisini tuketmis
gorunemez ve bu, veritabani seviyesinde IMKANSIZDIR.

`user_id` yabanci anahtarlari CIPLAK ve bu bir istisna DEGIL: `app_users`
firma sutunu TASIMAZ (0076/0077 ile AYNI gerekce), yani bilesik anahtar
KURULAMAZ.

--- AKTIF NUMARA TEKILLIGI: `(company_id, phone)` — KAYNAKTAN AYRILDI ---

Bu gocun kaynaktan (nazgul_website) BILINCLI olarak AYRILDIGI tek yer.

Kaynak `uq_whatsapp_links_active_phone` adinda KURESEL bir kismi tekil
tasiyor: aktif bir numara SISTEM GENELINDE tektir, `company_id` anahtarda
DEGILDIR. Gerekcesi kaynagin kendi yorumunda yazili: "WhatsApp mesajinda
guvenilir bir firma SECICI yoktur".

O gerekce BU DEPODA GECERLI DEGIL cunku bu goc secicinin kendisini de
getiriyor: `whatsapp_context` + `FIRMA SEC` komutu. Seciciyi getirip
kuresel tekili de korumak, muhasebecisi iki firmaya bakan bir kullanicinin
IKINCI firmasina hicbir zaman baglanamamasi demekti — ve o kullanici bu
urunun tipik kullanicisidir.

Tekil bu yuzden `(company_id, phone) WHERE is_active`tir: ayni numara
ayni firmada IKI KEZ aktif OLAMAZ (yoksa "bu numara kim" sorusunun o firma
icinde iki cevabi olurdu), ama BASKA bir firmada aktif OLABILIR.

KISMI (WHERE'li) TEKIL, DUZ TEKIL DEGIL — ve bu ZORUNLU.
`UNIQUE(company_id, phone, is_active)` yazsaydik pasif satirlar da
anahtara girerdi: ayni numara ayni firmada iki kez KAPATILAMAZDI, yani
gecmis izi silinmek zorunda kalirdi. Kismi indeks pasif satirlari anahtarin
DISINDA birakir.

Kismi tekil IKI DIYALEKTTE DE gercek: SQLite 3.8'den beri, PostgreSQL
her surumde kismi indeks destekliyor. Yuklem dialekte gore AYRI yaziliyor
(`is_active = 1` / `is_active = true`) cunku SQLite'in boolean'i bir
tamsayidir. Kapi: `tests/test_wa2_eslestirme.py::
test_AKTIF_NUMARA_TEKILI_SQLitede_hem_REDDEDIYOR_hem_IZIN_VERIYOR` ve PG
ikizindeki `test_AKTIF_NUMARA_TEKILI_gercekten_REDDEDIYOR` — IKI diyalekt
de hem REDDI hem IZNI olcuyor.

--- YEDI `ck_wpc_*` CHECK: DURUM x ZAMAN DAMGASI MATRISI ----------------

Kod satirinin durumu ile zaman damgalari AYRISAMAZ. `CONSUMED` ama
`consumed_at` NULL olan bir satir, "bu kod ne zaman kullanildi" sorusunu
cevapsiz birakir; `PENDING` ama `cancelled_at` dolu olan bir satir, iki
farkli okuyucuya iki farkli sey soyler. Matris CHECK ile veritabani
seviyesinde isiriyor:

  PENDING  : uctu de NULL
  CONSUMED : consumed_at + consumed_link_id dolu, cancelled_at NULL
  CANCELLED: cancelled_at dolu, consumed_* NULL
  EXPIRED  : uctu de NULL

--- BU GOCUN YAPMADIGI: ISCI, META CEVABI — OLCULMEDI ------------------

Bu turda isci YOKTUR ve Meta'ya cevap GONDERILMEZ. `app/whatsapp/
eslestirme.py` ve `app/whatsapp/baglam.py` SAF FONKSIYONLAR sunar;
`komut_isle` bir dize dondurur, hicbir yere gondermez. Kuyrugu bu
fonksiyonlara baglayan isci WA3'un isidir.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260910_0079"
down_revision = "20260910_0078"
branch_labels = None
depends_on = None

BAGLANTI = "whatsapp_links"
KOD = "whatsapp_pairing_codes"
BAGLAM = "whatsapp_context"

#: Kapali kume. Gerekce baslikta. `app/whatsapp/schema.py` ile BIREBIR ayni.
KOD_DURUMLARI = ("PENDING", "CONSUMED", "CANCELLED", "EXPIRED")

BAGLANTI_FIRMA_KIMLIK = "uq_whatsapp_links_company_id"
BAGLANTI_AKTIF_TEKIL = "uq_whatsapp_links_aktif_numara"
BAGLANTI_NUMARA_INDEKS = "ix_whatsapp_links_phone"

KOD_FIRMA_KIMLIK = "uq_wpc_company_id"
KOD_OZET_TEKIL = "uq_wpc_code_digest"
KOD_AKTIF_TEKIL = "uq_wpc_aktif_kod"
KOD_TARAMA_INDEKS = "ix_wpc_company_status"

BAGLAM_FIRMA_KIMLIK = "uq_whatsapp_context_company_id"
BAGLAM_KAPSAM_TEKIL = "uq_whatsapp_context_scope"


def _durum_check() -> str:
    return "status IN (%s)" % ",".join("'" + d + "'" for d in KOD_DURUMLARI)


def _aktif_yuklem(lehce: str):
    """Kismi indeksin WHERE'i — DIYALEKTE GORE AYRI ve bu ZORUNLU.

    SQLite'in boolean'i bir TAMSAYIDIR (`0`/`1`); `is_active = true` orada
    bir sozdizimi hatasi degil, `true` adinda COZULEMEYEN bir sutun
    referansidir. PostgreSQL'de ise `is_active = 1` tip hatasidir. Tek bir
    metin yazmak, indeksin bir diyalektte HIC KURULMAMASINA yol acardi —
    ve kurulmamis bir tekil, HICBIR SEYI reddetmez.
    """
    return sa.text("is_active = true" if lehce == "postgresql" else "is_active = 1")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    mevcut = set(inspector.get_table_names())

    # 0072'de olculen kusur (acilis DDL'i tabloyu gocten ONCE kurar, goc onu
    # VAR bulup atlar) bu uc tabloda URETILEMEZ: ucu de YALNIZ burada
    # doguyor ve hicbir `metadata.create_all` cagrisinin kapsaminda degil.
    # Kapi: `tests/test_wa2_eslestirme.py::test_ACILIS_DDLi_GOCUN_ONUNE_
    # GECMIYOR`.
    if BAGLANTI not in mevcut:
        op.create_table(
            BAGLANTI,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            # CIPLAK yabanci anahtar ve bu bir istisna DEGIL: `app_users`ta
            # `company_id` sutunu YOKTUR (0076/0077 ile AYNI gerekce).
            #
            # ON DELETE CASCADE: silinen kullanicinin numarasi defterde
            # KALSAYDI, o numaradan gelen mesaj var olmayan bir `user_id`ye
            # cozulur ve `kimlik_coz` her seferinde bos bir JOIN uretirdi.
            sa.Column("user_id", sa.Integer(), nullable=False),
            # Kanonik numara (`app/whatsapp/telefon.py::normalize_phone`):
            # ARTI ISARETSIZ rakam dizisi. WA1'in `sender_phone`u ile AYNI
            # genislik — iki taraf ayni degeri karsilastirmak zorunda.
            sa.Column("phone", sa.String(length=20), nullable=False),
            sa.Column(
                "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            # Baglantiyi acan yonetici. Kullanici silinince iz KALIR (SET
            # NULL): "kim baglamis" sorusunun cevabini kaybetmek, defteri
            # denetim icin degersiz yapardi.
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(
                ["user_id"], ["app_users.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["created_by"], ["app_users.id"], ondelete="SET NULL"
            ),
            # 0062'nin kurali: bilesik yabanci anahtar HEDEFI olabilmesi icin.
            sa.UniqueConstraint("company_id", "id", name=BAGLANTI_FIRMA_KIMLIK),
        )
        # `kimlik_coz` numaradan BUTUN firmalari tariyor (secici gelmeden once
        # kac aday oldugunu bilmek zorunda), yani yuklem YALNIZ `phone`dur ve
        # indeks kiraci sutunu TASIMAZ.
        op.create_index(BAGLANTI_NUMARA_INDEKS, BAGLANTI, ["phone"])
        # AKTIF NUMARA TEKILLIGI — gerekce baslikta. KISMI, duz degil.
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
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=True),
            # DUZ KOD ASLA SAKLANMAZ: yalniz SHA-256 hex ozeti (64 karakter,
            # `app/auth.py::token_digest` ile AYNI sozlesme). Ozet
            # `WHATSAPP_APP_SECRET`e BAGLANMAZ — App Secret rotasyonu
            # bekleyen kodlari gecersiz kilmamalidir.
            sa.Column("code_digest", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=12), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("consumed_link_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            # --- YEDI CHECK: DURUM x ZAMAN DAMGASI MATRISI (baslik) -------
            sa.CheckConstraint(_durum_check(), name="ck_wpc_status"),
            sa.CheckConstraint("attempt_count >= 0", name="ck_wpc_attempt_count"),
            sa.CheckConstraint("max_attempts > 0", name="ck_wpc_max_attempts"),
            sa.CheckConstraint(
                "(status <> 'PENDING') OR "
                "(consumed_at IS NULL AND cancelled_at IS NULL "
                "AND consumed_link_id IS NULL)",
                name="ck_wpc_pending_temiz",
            ),
            sa.CheckConstraint(
                "(status <> 'CONSUMED') OR "
                "(consumed_at IS NOT NULL AND consumed_link_id IS NOT NULL "
                "AND cancelled_at IS NULL)",
                name="ck_wpc_consumed_alanlari",
            ),
            sa.CheckConstraint(
                "(status <> 'CANCELLED') OR "
                "(cancelled_at IS NOT NULL AND consumed_at IS NULL "
                "AND consumed_link_id IS NULL)",
                name="ck_wpc_cancelled_alani",
            ),
            sa.CheckConstraint(
                "(status <> 'EXPIRED') OR "
                "(consumed_at IS NULL AND consumed_link_id IS NULL "
                "AND cancelled_at IS NULL)",
                name="ck_wpc_expired_temiz",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(
                ["user_id"], ["app_users.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["created_by"], ["app_users.id"], ondelete="SET NULL"
            ),
            # BILESIK: bir firmanin kodu BASKA firmanin baglantisini
            # tuketmis gorunemez (baslik).
            sa.ForeignKeyConstraint(
                ["company_id", "consumed_link_id"],
                ["%s.company_id" % BAGLANTI, "%s.id" % BAGLANTI],
            ),
            # KURESEL tekil ve bu BILINCLI: ozet tahmin edilerek aranir ve
            # arayan hangi firmaya yazdigini BILMEZ. Kiraci kapsamli bir
            # tekil, ayni ozetin iki firmada var olmasina izin verirdi ve o
            # hâlde `kod_kullan`in ozet aramasi IKI satir dondururdu.
            sa.UniqueConstraint("code_digest", name=KOD_OZET_TEKIL),
            sa.UniqueConstraint("company_id", "id", name=KOD_FIRMA_KIMLIK),
        )
        op.create_index(
            KOD_TARAMA_INDEKS, KOD, ["company_id", "status", "expires_at"]
        )
        # Firma+kullanici basina EN FAZLA BIR bekleyen kod. "Yeni kod
        # eskisini iptal eder" kuralinin hakemi uygulama sorgusu DEGIL bu
        # indekstir: iki yonetici ayni anda kod uretse bile tek bekleyen kod
        # kalir.
        op.create_index(
            KOD_AKTIF_TEKIL,
            KOD,
            ["company_id", "user_id"],
            unique=True,
            sqlite_where=sa.text("status = 'PENDING'"),
            postgresql_where=sa.text("status = 'PENDING'"),
        )

    if BAGLAM not in mevcut:
        op.create_table(
            BAGLAM,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("phone", sa.String(length=20), nullable=False),
            # JSON metni. YALNIZ uygulama yazar/okur; kullaniciya ham hâliyle
            # GOSTERILMEZ ve icinden hicbir YETKI iddiasi okunmaz —
            # `aktif_firma` degeri her okumada uyelige karsi DOGRULANIR.
            sa.Column("payload", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(
                ["user_id"], ["app_users.id"], ondelete="CASCADE"
            ),
            # Uclu basina TEK baglam: yenisi eskisini degistirir. "Aktif
            # firma" sorusunun her an tek cevabi olur.
            sa.UniqueConstraint(
                "company_id", "user_id", "phone", name=BAGLAM_KAPSAM_TEKIL
            ),
            sa.UniqueConstraint("company_id", "id", name=BAGLAM_FIRMA_KIMLIK),
        )


def downgrade() -> None:
    """Simetrik ve KOSULLU. Tablolari dusurmek VERI KAYBIDIR ve bilincli.

    Kaybin anlami olculu: silinen sey IS VERISI DEGIL, "bu numara kim"
    eslesmesidir. Geri alma sonrasi her kullanici YENIDEN kod alip
    baglanmak zorunda kalir — ve o ana kadar gelen mesajlar `kimlik_coz`da
    `None`a duser, yani sessizce IGNORED olur. Bu, gocten ONCEKI davranisin
    ta kendisi.

    SIRA TERS: once baglam ve kod defteri, en son baglanti. `whatsapp_
    pairing_codes` bilesik yabanci anahtarla `whatsapp_links`e bagli;
    baglantiyi once dusurmek PostgreSQL'de `DependentObjectsStillExist`
    verirdi (SQLite bunu gormezden gelirdi, yani kusur YALNIZ uretimde
    gorunurdu).
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    mevcut = set(inspector.get_table_names())

    if BAGLAM in mevcut:
        op.drop_table(BAGLAM)

    if KOD in mevcut:
        indeksler = {i["name"] for i in inspector.get_indexes(KOD)}
        for ad in (KOD_AKTIF_TEKIL, KOD_TARAMA_INDEKS):
            if ad in indeksler:
                op.drop_index(ad, table_name=KOD)
        op.drop_table(KOD)

    if BAGLANTI in mevcut:
        indeksler = {i["name"] for i in inspector.get_indexes(BAGLANTI)}
        for ad in (BAGLANTI_AKTIF_TEKIL, BAGLANTI_NUMARA_INDEKS):
            if ad in indeksler:
                op.drop_index(ad, table_name=BAGLANTI)
        op.drop_table(BAGLANTI)
