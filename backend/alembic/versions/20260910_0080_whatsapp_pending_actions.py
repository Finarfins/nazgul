"""WHATSAPP BEKLEYEN ISLEMLER: iki adimli yazmanin defteri (WA4).

Konu: bu goc SEMAYI kurar. Servis `app/whatsapp/bekleyen.py`dedir; UC YOKTUR
(bu PR hicbir rota eklemez — kapi: `tests/test_wa4_bekleyen.py::
test_WA4_HICBIR_ROTA_EKLEMEDI`).

--- OLCULEN EKSIK: NIYET VAR, TASLAK YOK ---------------------------------

WA3-core `app/whatsapp/niyet.py`e `tahsilat_coz`u ve `TahsilatNiyeti`yi
getirdi: "Saban Korkmaz 175.000 nakit verdi" cumlesi artik DETERMINISTIK
olarak tutara, yonteme ve musteri terimine cozuluyor. Ama o niyetin
gidecegi bir yer YOKTU — olculdu: `alembic/versions/` altinda
`whatsapp_pending_actions` adinda bir tablo YOKTU ve `app/whatsapp/`
altinda taslak acan tek bir fonksiyon bile YOKTU. `app/whatsapp/schema.py`
bu eksigi kendi basliginda ACIKCA yaziyordu:

    TASINMAYAN TEK TABLO ve gerekcesi (bu dilimin KAPSAMI DISINDA):
    * `whatsapp_pending_actions` — iki adimli yazma taslaklari; bu PR
      hicbir finansal yazma yapmiyor.

Bu goc o tabloyu aciyor. FINANSAL YAZMA ARTIK VAR ve tam olarak bu yuzden
IKI ADIMLIDIR: bir sohbet mesaji ASLA dogrudan para kaydetmez. Once taslak
(`PENDING`) ve ozet metni, sonra kullanicinin ACIK `ONAY`i.

--- KIRACI TABLOSU: `company_id` VAR -------------------------------------

Tablo `TENANT_TABLES` envanterine GIRER (envanter goc edilmis semadan
`company_id` sutunu tarayarak OTOMATIK turuyor) ve her sorgusundan
`company_id=:cid` yuklemi istenir. Karsiligi VARDIR: servisteki HER sorgu
`_kapsam()` uzerinden firma+kullanici+numara ucluusuyle daraliyor.

Sayi 119 -> 120 (kapilar: `tests/test_wa1_ingress.py`,
`tests/test_wa2_eslestirme.py`, `tests/test_wa4_bekleyen.py`).

--- BILESIK YABANCI ANAHTAR: 0062'NIN KURALI ----------------------------

`(company_id, whatsapp_link_id) -> whatsapp_links(company_id, id)`.

Ciplak bir `whatsapp_link_id -> whatsapp_links.id` YETMEZDI: bir firmanin
bekleyen islemi BASKA firmanin baglantisina asili gorunebilirdi. Bilesik
anahtarla bu VERITABANI SEVIYESINDE IMKANSIZDIR. Hedefin kendisi
(`uq_whatsapp_links_company_id`) 0079'da zaten kurulmustu — tam olarak bu
tur bir baglanti icin.

FK "satir VAR" der, "satir SENIN" DEMEZ. Aktiflik ve kullanici esitligi
FK'nin ANLATAMAYACAGI seylerdir ve servis tarafinda ayrica olculur
(`bekleyen._baglanti_dogrula`): AYNI firma + AYNI kullanici + AYNI kanonik
numara + AKTIF. Iki koruma AYNI seyi olcmuyor, bu yuzden ikisi de var.

--- `islem_anahtari` UNIQUE: IDEMPOTENSININ KOKU ------------------------

Bu sutun bir gorunum alani DEGIL, PARA GUVENLIGININ TASIYICISIDIR.

`bekleyen.onayla` odemeyi `payment_allocation_engine.
create_payment_with_allocation`a `f"wa:{islem_anahtari}"` anahtariyla
yazar. O anahtar `payment_idempotency` defterinde
`(company_id,'create_payment','payment','create',key)` olarak tekildir
(olculdu: `_claim_payment_create`), yani AYNI taslagin ikinci uygulamasi
YENI BIR ODEME URETMEZ — saklanmis sonucu geri oynatir.

Bunun calismasi icin anahtarin taslak omru boyunca SABIT olmasi sart.
Uygulama aninda uretilen taze bir UUID kullansaydik, isci odemeyi yazip
`APPLIED` damgasini yazamadan cokerse, lease devralmasindan sonraki ikinci
deneme YENI bir anahtarla gider ve MUSTERIYE IKINCI KEZ TAHSILAT
ISLENIRDI. Satirda dogan, satirla yasayan bir anahtar bu pencereyi kapatir.

UNIQUE KURESELDIR (`company_id` anahtarda YOK) ve bu bilinclidir: deger
`uuid4().hex`tir, yani carpismasi pratikte imkansizdir; kiraci sutunu
eklemek tekilligi ZAYIFLATIR (ayni anahtar iki firmada yasayabilirdi) ve
odeme defterinin anahtari zaten firma ile birlikte kapsamlanmistir.

--- KISMI TEKIL: KAPSAMDA TEK AKTIF TASLAK -----------------------------

`UNIQUE(company_id, user_id, phone) WHERE status IN ('PENDING','APPLYING')`.

Kural: bir kullanicinin bir numarasinda AYNI ANDA EN FAZLA BIR aktif
taslak olabilir. Hakem uygulama sorgusu DEGIL, bu indekstir — iki mesaj
ayni anda gelse bile ikinci taslak `IntegrityError`a carpar ve
`BekleyenIslemSurmekte`ye cevrilir.

Neden onemli: aktif taslak TEK olmasaydi, kullanicinin yazdigi `ONAY`
kelimesinin HANGI taslaga ait oldugu BELIRSIZ olurdu. Sohbette bir secim
arayuzu yoktur; tekillik, "ONAY" kelimesinin tek bir anlaminin olmasini
saglayan seydir. Belirsiz bir onayin PARA yazmasi, bu tablonun onlemek
icin var oldugu seyin ta kendisidir.

KISMI (WHERE'li), DUZ DEGIL — ve bu ZORUNLU. `UNIQUE(company_id,user_id,
phone,status)` yazsaydik terminal satirlar da anahtara girerdi: ayni
kullanici ayni numaradan IKINCI KEZ tahsilat yapamazdi (ilk `APPLIED`
satir anahtari sonsuza dek tutardi). Kismi indeks terminal satirlari
anahtarin DISINDA birakir; gecmis iz KALIR.

Kismi tekil IKI DIYALEKTTE DE gercek: SQLite 3.8'den beri, PostgreSQL her
surumde kismi indeks destekliyor. 0079'un `_aktif_yuklem`inden AYRILAN bir
nokta var ve kayda deger: ORADA yuklem diyalekte gore AYRI yazilmak
ZORUNDAYDI cunku SQLite'in boolean'i bir tamsayidir (`is_active = 1` /
`is_active = true`). BURADA yuklem METIN karsilastirmasidir
(`status IN ('PENDING','APPLYING')`) ve iki diyalektte BIREBIR AYNI
sozdizimidir — yine de ayni `sa.text` nesnesi HER IKI parametreye de
ACIKCA veriliyor, cunku birini yazip otekini unutmak indeksin bir
diyalektte HIC KURULMAMASI demektir ve kurulmamis bir tekil HICBIR SEYI
reddetmez. Kapilar: `tests/test_wa4_bekleyen.py::
test_KISMI_TEKIL_ikinci_taslagi_REDDEDIYOR_terminale_IZIN_VERIYOR` ve PG
ikizindeki `test_KISMI_TEKIL_gercekten_REDDEDIYOR`.

--- DURUM KUMESI: ALTI DEGER, UCU TERMINAL ------------------------------

    PENDING   -> APPLYING  (ONAY; tek UPDATE'lik CAS, kazanan claim_token alir)
    PENDING   -> CANCELLED (IPTAL)
    PENDING   -> EXPIRED   (supurucu ya da okuma anindaki tembel kapatma)
    APPLYING  -> APPLIED   (odeme yazildi; `result_id` dolu)
    APPLYING  -> FAILED    (kalici hata; `fail_reason` SINIF adi)
    APPLYING  -> PENDING   (gecici hata; taslak omru icinde yeniden denenir)

`SUPERSEDED` YOKTUR ve bu kaynaktan (nazgul_website) AYRILAN yerdir:
orada yeni taslak eskisini deterministik olarak degistiriyordu. Burada
kismi tekil ikinci taslagi REDDEDER. Gerekce yukarida: "ONAY"in tek bir
anlami olmali; sessizce degistirilen bir taslak, kullanicinin ekraninda
HALA duran eski ozeti onaylamasina ve BASKA bir tutarin yazilmasina yol
acabilirdi.

`fail_reason` yalniz Python istisna SINIFI adidir (servis dogrular). 64
karakter, bir sinif adi icin fazlasiyla yeter ve hassas hata metninin
(`DB password=...`) buraya sigmasini da engeller.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260910_0080"
down_revision = "20260910_0079"
branch_labels = None
depends_on = None

BEKLEYEN = "whatsapp_pending_actions"

#: Kapali kume. `app/whatsapp/schema.py` ile BIREBIR ayni.
ISLEM_TURLERI = ("TAHSILAT",)
DURUMLAR = ("PENDING", "APPLYING", "APPLIED", "CANCELLED", "EXPIRED", "FAILED")
#: Kismi tekilin kapsami: "aktif" sayilan iki durum.
AKTIF_DURUMLAR = ("PENDING", "APPLYING")

BEKLEYEN_FIRMA_KIMLIK = "uq_wpa_company_id"
BEKLEYEN_ANAHTAR_TEKIL = "uq_wpa_islem_anahtari"
BEKLEYEN_AKTIF_TEKIL = "uq_wpa_aktif_taslak"
BEKLEYEN_SUPURME_INDEKS = "ix_wpa_status_expires"


def _liste(degerler: tuple[str, ...]) -> str:
    return ",".join("'" + d + "'" for d in degerler)


def _aktif_yuklem():
    """Kismi indeksin WHERE'i — IKI DIYALEKTTE DE AYNI METIN.

    0079'un `_aktif_yuklem`inden AYRILIYOR: orada yuklem bir BOOLEAN
    karsilastirmasiydi ve diyalekte gore AYRI yazilmak zorundaydi. Burada
    METIN karsilastirmasidir, yani tek bir ifade her iki diyalektte de
    gecerlidir. Yine de asagida HER IKI parametreye de ACIKCA veriliyor:
    birini vermeyi unutmak, indeksin o diyalektte HIC kurulmamasi demektir.
    """
    return sa.text("status IN (%s)" % _liste(AKTIF_DURUMLAR))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    mevcut = set(inspector.get_table_names())

    # 0072'de olculen kusur (acilis DDL'i tabloyu gocten ONCE kurar, goc onu
    # VAR bulup atlar) burada URETILEMEZ: tablo YALNIZ burada doguyor ve
    # hicbir `metadata.create_all` cagrisinin kapsaminda degil. Kapi:
    # `tests/test_wa4_bekleyen.py::test_ACILIS_DDLi_GOCUN_ONUNE_GECMIYOR`.
    if BEKLEYEN in mevcut:
        return

    op.create_table(
        BEKLEYEN,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        # CIPLAK yabanci anahtar ve bu bir istisna DEGIL: `app_users`ta
        # `company_id` sutunu YOKTUR (0076/0077/0079 ile AYNI gerekce).
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("whatsapp_link_id", sa.Integer(), nullable=False),
        # Kanonik numara (`app/whatsapp/telefon.py::normalize_phone`): ARTI
        # ISARETSIZ rakam dizisi. `whatsapp_links.phone` ile AYNI genislik.
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("action_type", sa.String(length=30), nullable=False),
        # JSON metni. PARA DEGERLERI METIN olarak yazilir (servis `float`
        # yuku REDDEDER): JSON sayisina donusen bir tutar ikili kayan nokta
        # olur ve 175000.00 geri okundugunda 174999.99999 olabilirdi.
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        # Gerekce baslikta: odeme idempotensisinin KOKU.
        sa.Column("islem_anahtari", sa.String(length=64), nullable=False),
        # Lease sahipligi. Tamamlama YALNIZ jeton sahibine aciktir; jeton
        # `hmac.compare_digest` ile karsilastirilir (servis).
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        # Yazilan odemenin kimligi (`payments.id`). YABANCI ANAHTAR DEGIL ve
        # bu olculmus bir karar: `payments` satiri silinebilir (DELETE
        # /api/payments/{id} var) ve silindiginde bu satirin "uygulandi" izi
        # KAYBOLMAMALIDIR. FK CASCADE izi silerdi, RESTRICT ise odemenin
        # silinmesini bir sohbet taslagi yuzunden engellerdi.
        sa.Column("result_id", sa.Integer(), nullable=True),
        # YALNIZ Python istisna SINIFI adi (servis dogrular ve biçime
        # uymayan her degeri sabit guvenli sinifa cevirir).
        sa.Column("fail_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], ondelete="CASCADE"),
        # 0062'nin kurali, TERSTEN: burada bilesik anahtarin KAYNAGIYIZ.
        # Hedef `uq_whatsapp_links_company_id` 0079'da kuruldu.
        sa.ForeignKeyConstraint(
            ["company_id", "whatsapp_link_id"],
            ["whatsapp_links.company_id", "whatsapp_links.id"],
            name="fk_wpa_link",
        ),
        sa.CheckConstraint(
            "action_type IN (%s)" % _liste(ISLEM_TURLERI),
            name="ck_wpa_action_type",
        ),
        sa.CheckConstraint(
            "status IN (%s)" % _liste(DURUMLAR), name="ck_wpa_status"
        ),
        # DURUM x ALAN MATRISI — `APPLIED` bir `result_id` TASIMAK ZORUNDA.
        # Tasimasaydi "uygulandi ama hangi odeme" sorusunun cevabi olmazdi
        # ve mutabakat imkansizlasirdi.
        sa.CheckConstraint(
            "(status <> 'APPLIED') OR (result_id IS NOT NULL)",
            name="ck_wpa_applied_result",
        ),
        # Terminal satirda lease ARTIGI KALMAZ: kalsaydi `claim_et`in
        # devralma kolu terminal bir satiri APPLYING'e geri cekebilirdi.
        sa.CheckConstraint(
            "(status IN ('PENDING','APPLYING')) OR "
            "(claim_token IS NULL AND claim_expires_at IS NULL)",
            name="ck_wpa_terminal_lease_temiz",
        ),
        sa.UniqueConstraint("islem_anahtari", name=BEKLEYEN_ANAHTAR_TEKIL),
        # 0062'nin kurali: bilesik yabanci anahtar HEDEFI olabilmesi icin.
        sa.UniqueConstraint("company_id", "id", name=BEKLEYEN_FIRMA_KIMLIK),
    )

    # KAPSAMDA TEK AKTIF TASLAK — gerekce baslikta. KISMI, duz degil.
    op.create_index(
        BEKLEYEN_AKTIF_TEKIL,
        BEKLEYEN,
        ["company_id", "user_id", "phone"],
        unique=True,
        sqlite_where=_aktif_yuklem(),
        postgresql_where=_aktif_yuklem(),
    )
    # Supurucunun (`bekleyen.suresi_gecenleri_kapat`) tek yuklemi
    # `status='PENDING' AND expires_at <= now`. Kiraci sutunu TASIMAZ ve
    # tasimamasi dogrudur: supurucu KURESEL kosar (bir isci, butun
    # firmalarin suresi gecmis taslaklarini kapatir).
    op.create_index(BEKLEYEN_SUPURME_INDEKS, BEKLEYEN, ["status", "expires_at"])


def downgrade() -> None:
    """Simetrik ve KOSULLU. Tabloyu dusurmek VERI KAYBIDIR ve bilincli.

    Kaybin anlami olculu ve DAR: silinen sey ODEME DEGIL, odemeye giden
    TASLAKTIR. `APPLIED` satirlarin yazdigi `payments` kayitlari YERINDE
    KALIR (`result_id` bilerek yabanci anahtar DEGIL, baslikta gerekcesi
    var) — yani geri alma hicbir parayi geri almaz, yalnizca "bu odeme
    WhatsApp'tan geldi" izini siler.

    Bekleyen (`PENDING`) taslaklar kaybolur ve bu DOGRU davranistir: geri
    alma sonrasi kullanicinin yazacagi `ONAY` kelimesinin karsiligi olmaz
    ve mesaj sessizce dusuurulur — yani gocten ONCEKI davranisin ta kendisi.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if BEKLEYEN not in set(inspector.get_table_names()):
        return
    indeksler = {i["name"] for i in inspector.get_indexes(BEKLEYEN)}
    for ad in (BEKLEYEN_AKTIF_TEKIL, BEKLEYEN_SUPURME_INDEKS):
        if ad in indeksler:
            op.drop_index(ad, table_name=BEKLEYEN)
    op.drop_table(BEKLEYEN)
