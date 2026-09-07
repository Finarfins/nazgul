"""GENEL İDEMPOTENSİ DEFTERİ: `Idempotency-Key` başlığının TEK deposu (5.4b).

Konu: bu göç ŞEMAYI kurar; davranış `app/idempotency.py`de ve `app/main.py`nin
`security_and_audit` ara katmanındadır.

--- ÖLÇÜLEN KUSUR: BEŞ AYRI DEFTER, ORTAK BİR KAPI YOK -------------------

Depoda ölçüldü (`alembic/versions/` içinde `*_idempotency` tabloları): BEŞ
tane var ve BEŞİ DE FARKLI ŞEKİLDE:

  * `machine_idempotency`            (company_id, idempotency_key) + machine_id
  * `pos_idempotency`                (company_id, idempotency_key) + order_id
  * `purchase_draft_idempotency`     (company_id, idempotency_key) + purchase_ids
  * `payment_idempotency`            (company_id, operation_id) + durum sütunu
  * `receivable_charge_idempotency`  (company_id, operation_id) + durum sütunu

Her biri KENDİ ucunun SONUCUNU saklıyor (bir kimlik, bir kimlik listesi, bir
durum), yani hiçbiri BAŞKA bir ucun cevabını tekrar oynatamaz. HTTP katmanında
ise HİÇBİR ŞEY yoktu: ölçüldü, `app/main.py`de `Idempotency-Key` literali
SIFIR kez geçiyordu.

Tarla/hayvan tarafında ÜÇÜNCÜ bir desen daha var (`operation_id` GÖVDEDE:
`app/routers/farm.py`, `app/routers/field.py`). O desen BU GÖÇLE KIMILDAMADI
ve gövdeye taşınamazdı: `app/herd_schemas.py` `extra="forbid"` kullanıyor,
yani gövdeye alan eklemek KAPALI bir sözleşmeyi kırardı. Başlık ise gövdeye
DOKUNMAZ — genel çözümün BAŞLIKTA olmasının ölçülmüş sebebi budur.

--- NEDEN TEK TABLO, ALTINCI BİR "*_idempotency" DEĞİL -------------------

Beş defterin ortak kusuru şudur: her biri kendi ucunun İŞ SONUCUNU (bir satır
kimliği) tutar ve o kimlikten cevabı YENİDEN KURMAK zorundadır — `pos.py`nin
`_idempotent_replay`i tam olarak bunu yapıyor ve o yüzden POS'a özeldir.

Bu tablo İŞ SONUCUNU DEĞİL, HTTP CEVABININ KENDİSİNİ (`response_status` +
`response_body`) saklıyor. Bu, TEK bir ara katmanın kendi ucuna özel hiçbir
şey bilmeden çalışabilmesinin tek yoludur: cevabı yeniden kurmak uca özel
bilgi ister, cevabı SAKLAMAK istemez.

Beş defter YERİNDE KALIYOR ve bu göç onlara DOKUNMUYOR. Onları bu tabloya göç
ettirmek, çalışan ve test edilmiş beş yolu tek turda değiştirmek olurdu;
ayrıca ikisi (`payment_idempotency`, `receivable_charge_idempotency`)
`operation_id`yi GÖVDEDEN alıyor ve başlık defteri o alanı HİÇ görmez.

--- ANAHTARIN KAPSAMI: (FİRMA, KULLANICI, ANAHTAR) ------------------------

Tekillik ÜÇ sütunludur ve üçünün de gerekçesi ÖLÇÜLEBİLİR:

  * `company_id` — kiracı sınırı. Düşerse bir firmanın gönderdiği anahtar
    BAŞKA firmanın isteğini 409'a düşürürdü; daha kötüsü, hash tutarsa o
    firmanın CEVABINI tekrar oynatırdı. Bu, kiracı sızıntısının ta kendisidir.
  * `user_id` — anahtarı istemci KENDİ yerelinde üretir (uuid4 beklenir ama
    DAYATILAMAZ). Kullanıcı düşerse aynı firmadaki iki kullanıcının çakışan
    anahtarı birbirinin isteğini yutardı.
  * `key` — istemcinin söylediği.

`UNIQUE(company_id, user_id, key)` bu yüzden ARA KATMANIN EŞZAMANLILIK
SINIRIDIR: iddia (`claim`) bir INSERT'tür ve yarışı veritabanı çözer.

`UNIQUE(company_id, id)` 0062'nin kuralı gereği: ileride bileşik yabancı
anahtar hedefi olabilmesi için.

`user_id`in yabancı anahtarı ÇIPLAKTIR ve bu bir istisna DEĞİL, olgunun
kendisidir: `app_users` tablosunda `company_id` sütunu YOKTUR (ölçüldü), yani
bileşik anahtar KURULAMAZ. Kiracı sınırı bu tabloda `company_id` sütunuyla ve
onu HER sorguda taşıyan yüklemle korunuyor.

--- `status` KAPALI KÜMESİ: processing | completed -------------------------

İki değer var ve ÜÇÜNCÜSÜ BİLEREK YOK. Özellikle `failed` YOK: 5xx cevaplar
SAKLANMAZ, satır SİLİNİR. Gerekçe ölçülebilir — saklanan bir 5xx, istemcinin
tekrar denemesini SONSUZA KADAR aynı 5xx'e mahkûm ederdi ve idempotensinin
amacı tekrarı GÜVENLİ kılmaktır, İMKÂNSIZ kılmak değil.

`processing` satırı KENDİLİĞİNDEN düşmez. İşleyici tamamlandığı hâlde
tamamlama yazımı düşerse (süreç öldü, bağlantı koptu) anahtar `expires_at`e
kadar `processing` kalır ve o süre boyunca 409 döner. BU PENCERE BİLİNÇLİDİR
ve alternatifinden iyidir: satırı silmek, tamamlanmış bir yazmanın ikinci kez
koşmasına izin vermek demekti.

--- TTL 24 SAAT, SÜPÜRGE YOK (BU TURDA ÖLÇÜLMEDİ) ------------------------

`expires_at` = yazım anı + 24 saat. Süresi dolmuş bir anahtar YENİ sayılır ve
temizliği TEMBELDİR: `app/idempotency.py` iddia etmeden ÖNCE tam olarak O
ANAHTARIN süresi dolmuş satırını siler. Yani defter kendini anahtar başına
toplar.

ZAMANLANMIŞ BİR SÜPÜRGE BU TURDA YOK ve bu bir eksiklik olarak YAZILIYOR: bir
daha hiç kullanılmayan anahtarların satırları kalır. `ix_idempotency_keys_expires_at`
indeksi tam olarak o süpürgenin taraması için açılıyor — bugün kullanan yok,
yarın kullanacak olan var. İndeksi bugün açmak, süpürgeyi ekleyen turun ŞEMA
GÖÇÜ AÇMASINI gereksiz kılar.

--- SAYISAL MANİFESTO ------------------------------------------------------

Bu göç TEK BİR SAYISAL (NUMERIC) SÜTUN AÇMIYOR: idempotensi defteri bir METİN
ve ZAMAN defteridir, bir tutar değil. `core_schema.py`ye DOKUNULMUYOR, yani
`capture_numeric_snapshot` için varlık farkı ÜRETİLMİYOR.

Revision ID: 20260909_0076
Revises: 20260909_0075
"""
from alembic import op
import sqlalchemy as sa

revision = "20260909_0076"
down_revision = "20260909_0075"
branch_labels = None
depends_on = None

DEFTER = "idempotency_keys"

#: Kapalı küme. ÜÇÜNCÜ değer YOK — gerekçe başlıkta.
DURUMLAR = ("processing", "completed")

DURUM_CHECK = "ck_idempotency_keys_status"
TEKIL_ANAHTAR = "uq_idempotency_keys_company_user_key"
FIRMA_KIMLIK = "uq_idempotency_keys_company_id"
SURE_INDEKS = "ix_idempotency_keys_expires_at"


def _durum_check() -> str:
    degerler = ",".join("'" + d + "'" for d in DURUMLAR)
    return "status IN (%s)" % degerler


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # TABLO BU GÖÇLE DOĞUYOR ve açılış DDL'inde BİLDİRİLMİYOR (kapı:
    # `tests/test_54b_idempotency.py::test_ACILIS_DDLi_GOCUN_ONUNE_GECMIYOR`).
    # 0072'de ölçülen kusur — açılış tabloyu göçten önce kurar, göç onu VAR
    # bulup atlar — bu yüzden burada ÜRETİLEMİYOR.
    if DEFTER in set(inspector.get_table_names()):
        return

    op.create_table(
        DEFTER,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        # ÇIPLAK yabancı anahtar ve bu bir istisna DEĞİL: `app_users`ta
        # `company_id` sütunu YOKTUR, yani bileşik anahtar KURULAMAZ.
        sa.Column("user_id", sa.Integer(), nullable=False),
        # İstemcinin söylediği. 128 karakter: uuid4'ün kanonik biçimi 36,
        # ULID 26; 128 elle yazılmış bir anahtara da yer bırakır ve
        # `machine_idempotency`nin 255'inden DAR seçildi çünkü bu sütun TEKİL
        # İNDEKSE giriyor ve indeks genişliği her yazımın maliyetidir.
        sa.Column("key", sa.String(length=128), nullable=False),
        # HANGİ İSTEK olduğu SAKLANIYOR: aynı anahtarın BAŞKA bir istekle
        # kullanılması reddedilecekse, neyin "aynı" sayıldığı denetimde
        # görünmek ZORUNDA.
        sa.Column("method", sa.String(length=8), nullable=False),
        # SOMUT yol (`/api/payments/42`), şablon DEĞİL: ara katman
        # YÖNLENDİRMEDEN ÖNCE koşar ve o an şablonu BİLMEZ.
        sa.Column("route", sa.String(length=200), nullable=False),
        # sha256 = 64 onaltılık karakter. CHAR: uzunluk SABİTTİR.
        sa.Column("request_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        # CEVABIN KENDİSİ. NULL = "cevap SAKLANMADI" ve bu, "cevap yok"tan
        # FARKLI bir olgudur; ayrımı `app/idempotency.py` taşıyor.
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_durum_check(), name=DURUM_CHECK),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"]),
        # ARA KATMANIN EŞZAMANLILIK SINIRI — gerekçe başlıkta.
        sa.UniqueConstraint("company_id", "user_id", "key", name=TEKIL_ANAHTAR),
        sa.UniqueConstraint("company_id", "id", name=FIRMA_KIMLIK),
    )
    # Bugün kullanan YOK; süpürgeyi ekleyecek turun göç AÇMASINI gereksiz
    # kılmak için bugün açılıyor — gerekçe başlıkta.
    op.create_index(SURE_INDEKS, DEFTER, ["expires_at"])


def downgrade() -> None:
    """Simetrik ve KOŞULLU. Tabloyu düşürmek VERİ KAYBIDIR ve bilinçlidir.

    `idempotency_keys` bu göçle DOĞDU; düşürmek göçten sonra iddia edilmiş her
    anahtarı siler. Kaybın anlamı DAR: silinen şey iş verisi DEĞİL, "bu istek
    zaten uygulandı" bilgisidir. Geri alma sonrası atılan bir tekrar istek
    ikinci kez koşar — ve bu, göçten ÖNCEKİ davranışın ta kendisidir.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if DEFTER not in set(inspector.get_table_names()):
        return
    mevcut = {i["name"] for i in inspector.get_indexes(DEFTER)}
    if SURE_INDEKS in mevcut:
        op.drop_index(SURE_INDEKS, table_name=DEFTER)
    op.drop_table(DEFTER)
