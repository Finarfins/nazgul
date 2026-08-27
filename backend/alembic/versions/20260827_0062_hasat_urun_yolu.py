"""Hasadın ürüne giden yolu: `crop_seasons.product_id` (NULL kabul eden).

Konu: FIELD_STOCK_OUTBOX açılış koşulu **1**.

--- ÖLÇÜLEN KUSUR ------------------------------------------------------------

`field_stok_tuketici` iki kaynak tipi tanıyor (`_KAYNAK`): `field_activity` ve
`field_harvest`. Faaliyet ÇALIŞIYOR — `field_activity_inputs.product_id` var ve
`_faaliyet_kalemleri` onu okuyor. Hasadın ürüne giden yolu YOKTU:

* `field_harvests` (0044) yalnız `companies` ve `(company_id, season_id) ->
  crop_seasons` yabancı anahtarlarını taşıyor; `product_id` YOK.
* `crop_seasons.crop` SERBEST METİN (`VARCHAR(120)`), `products`a bağı YOK.

Sonuç ölçüldü: HER `field_harvest` olayı terminal `SKIPPED_NO_PRODUCT`
kovasına düşüyordu — hasat stok ÜRETİR (`_KAYNAK` yönü +1) ama üretilecek
ürün bilinmiyordu.

--- KARAR: ÜRÜNÜ SEZON BİLDİRİR, HASAT DEVRALIR -----------------------------

Sütun HASADA değil SEZONA konuyor. Gerekçe:

* Sezon başına BİR karar; hasat başına bir karar değil. Bir sezon onlarca
  hasat satırı üretir, hepsi aynı ürünü verir.
* Mevcut sezonlar TOPLU doldurulabilir; mevcut hasatlar doldurulamazdı.
* Hasat başına ÜSTÜNE YAZMA (override) sonradan eklenebilir ve bu kararı
  GEÇERSİZ KILMAZ — saf ekleme olur.

--- SÜTUN NULL KABUL EDER; `SKIPPED_NO_PRODUCT` ERİŞİLEBİLİR KALIR ----------

Bu göç kovayı KALDIRMIYOR, KAÇINILABİLİR yapıyor. Ürünü bildirilmemiş bir
sezonun hasadı hâlâ `SKIPPED_NO_PRODUCT`a düşer ve düşmelidir: kovayı
kaldırmak, ADI KONMUŞ ve SAYILAN bir sonucu SESSİZ bir sonuca çevirirdi.
Sütunu `NOT NULL` yapmak da aynı kapıya çıkardı — mevcut sezonlara bir ürün
UYDURMAK gerekirdi.

--- BİLEŞİK YABANCI ANAHTAR, ÇIPLAK OLANI DEĞİL ----------------------------

Kısıt `(company_id, product_id) -> products(company_id, id)`. Çıplak
`product_id -> products.id` bir kiracının sezonunun BAŞKA kiracının ürününü
işaret etmesini ENGELLEMEZ; bileşik olan engeller. Hedefin tekil olması
gerektiği için `products`a önce `UNIQUE(company_id, id)` ekleniyor — desen
20260812_0058'de dört ebeveyn için kuruldu, burada beşincisine uygulanıyor.

0044, `field_activity_inputs.product_id` için ÇIPLAK anahtarı seçmiş ve
gerekçesini "mevcut `products` tablosuna kısıt eklemek yerine uygulama
katmanı doğrulaması" diye yazmıştı. O karar 0058'den ÖNCEDİR; 0058 aynı
tabloya-kısıt-ekleme işini dört ebeveynde ölçülü biçimde yaptı ve deseni
kurdu. Buradaki seçim o desenle hizalıdır. `field_activity_inputs`in çıplak
anahtarı BU GÖÇTE DEĞİŞTİRİLMİYOR — kapsam koşul 1'dir, geriye dönük
sertleştirme değil.

NULL bileşik anahtar: SQL varsayılanı MATCH SIMPLE'dır — demetin HERHANGİ bir
sütunu NULL ise kısıt HİÇ denetlenmez. `company_id` zaten `NOT NULL` olduğu
için tek NULL olabilen sütun `product_id`dir; yani "ürün bildirilmemiş" satır
serbestçe geçer, "ürün bildirilmiş" satır TAM denetlenir. İstenen tam budur.

--- SQLite: TABLO YENİDEN KURULUR, PRAGMA KAPATILMAK ZORUNDA ---------------

SQLite'ta bileşik yabancı anahtar bir TABLO KISITIDIR ve `ALTER TABLE ADD
CONSTRAINT` yoktur; alembic batch modu tabloyu YENİDEN KURAR (kopyala, DROP,
RENAME). `app/db.py` her SQLite bağlantısında `PRAGMA foreign_keys=ON` yapar.

ÖLÇÜLDÜ (bu depo, 0061'de bir SQLite veritabanı, `field_harvests` içinde bir
satır varken):

    sqlalchemy.exc.IntegrityError: (sqlite3.IntegrityError)
        FOREIGN KEY constraint failed
    [SQL: DROP TABLE crop_seasons]

`PRAGMA defer_foreign_keys=ON` bunu KURTARMIYOR (ayrıca ölçüldü): `DROP
TABLE`ın örtük silmesi ertelenmiyor. Çare, SQLite'ın KENDİ belgelediği tablo
yeniden kurma yordamıdır: pragma'yı KAPAT, kur, `PRAGMA foreign_key_check`
ile KIRIK REFERANS KALMADIĞINI ÖLÇ, pragma'yı GERİ AÇ.

İKİ ŞEY GECE 3'TE ÖNEMLİ OLACAK:

1. **`foreign_key_check` ATLANAMAZ.** Pragma kapalıyken yeniden kurulum
   sessizce kırık referans bırakabilir; kontrol olmadan bu ancak aylar sonra,
   rastgele bir sorguda görünürdü. Kırık referans bulunursa göç DURUR.
2. **PRAGMA GERİ AÇILMAK ZORUNDA.** Bağlantı HAVUZLUDUR: kapalı bırakılan
   pragma o bağlantının ÖMRÜ BOYUNCA bütün uygulamada yabancı anahtar
   denetimini sessizce kapatırdı. Ölçüldü: geri açılmayan bir koşumda
   çapraz-firma ürün işaret eden bir sezon KABUL EDİLDİ; geri açılan koşumda
   `IntegrityError` ile REDDEDİLDİ. Bu yüzden geri açma `finally`dedir.

PostgreSQL'de yeniden kurulum YOKTUR — `ALTER TABLE ... ADD CONSTRAINT`
yerinde çalışır — ve pragma da yoktur; yordamın tamamı diyalekte bağlıdır.

--- GERİ ALMA ---------------------------------------------------------------

`downgrade` sütunu, kısıtı, indeksi ve `products` üzerindeki UNIQUE'i düşürür.
VERİ KAYBI VARDIR ve adı konmuştur: sezonların ürün bildirimi silinir. Şema
0061'e döndükten sonra her hasat olayı yine `SKIPPED_NO_PRODUCT`a düşer —
yani geri alma, tüketiciyi 0061'deki ölçülmüş davranışına geri getirir,
kırmaz.

Revision ID: 20260827_0062
Revises: 20260824_0061
"""
from alembic import op
import sqlalchemy as sa

revision = "20260827_0062"
down_revision = "20260824_0061"
branch_labels = None
depends_on = None

SEZON = "crop_seasons"
URUN = "products"
SUTUN = "product_id"
UQ_AD = "uq_products_company_id"
FK_AD = "fk_crop_seasons_product_same_company"
INDEKS_AD = "ix_crop_seasons_company_product"


def _sutunlar(inspector, tablo: str) -> set[str]:
    return {sutun["name"] for sutun in inspector.get_columns(tablo)}


def _tekiller(inspector, tablo: str) -> set[str]:
    return {item["name"] for item in inspector.get_unique_constraints(tablo)}


def _yabanci_anahtarlar(inspector, tablo: str) -> set[str]:
    return {
        item["name"] for item in inspector.get_foreign_keys(tablo) if item.get("name")
    }


def _indeksler(inspector, tablo: str) -> set[str]:
    return {item["name"] for item in inspector.get_indexes(tablo)}


class _YenidenKurulumKapisi:
    """SQLite'ta yabancı anahtar denetimini KAPATIR, ölçer ve GERİ AÇAR.

    PostgreSQL'de hiçbir şey yapmaz: orada yeniden kurulum yoktur ve pragma da
    yoktur. Bkz. başlıktaki "SQLite: TABLO YENİDEN KURULUR" bölümü.
    """

    def __init__(self, bind) -> None:
        self._bind = bind
        self._sqlite = bind.dialect.name == "sqlite"

    def __enter__(self) -> "_YenidenKurulumKapisi":
        if self._sqlite:
            self._bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
        return self

    def __exit__(self, tip, deger, iz) -> bool:
        if not self._sqlite:
            return False
        try:
            # Yalnız gövde SORUNSUZ bittiyse ölç: zaten patlamış bir göçün
            # üstüne ikinci bir hata koymak, ilkini gizler.
            if tip is None:
                kirik = self._bind.exec_driver_sql(
                    "PRAGMA foreign_key_check"
                ).fetchall()
                if kirik:
                    raise RuntimeError(
                        "SQLite yeniden kurulumu KIRIK REFERANS bıraktı; göç "
                        f"durdu. PRAGMA foreign_key_check: {kirik[:5]!r}"
                    )
        finally:
            # HAVUZLU BAĞLANTI: bu satır atlanırsa yabancı anahtar denetimi
            # bütün uygulamada sessizce kapalı kalır (ölçüldü, bkz. başlık).
            self._bind.exec_driver_sql("PRAGMA foreign_keys=ON")
        return False


def upgrade() -> None:
    bind = op.get_bind()

    with _YenidenKurulumKapisi(bind):
        # --- 1. Ebeveyn: bileşik yabancı anahtarın hedefi tekil olmalı -----
        inspector = sa.inspect(bind)
        if UQ_AD not in _tekiller(inspector, URUN):
            with op.batch_alter_table(URUN) as batch_op:
                batch_op.create_unique_constraint(UQ_AD, ["company_id", "id"])

        # --- 2. Sezona sütun: NULL KABUL EDEN ------------------------------
        inspector = sa.inspect(bind)
        if SUTUN not in _sutunlar(inspector, SEZON):
            op.add_column(SEZON, sa.Column(SUTUN, sa.Integer(), nullable=True))

        # --- 3. Bileşik yabancı anahtar; hedefini 1. adım kurdu ------------
        inspector = sa.inspect(bind)
        if FK_AD not in _yabanci_anahtarlar(inspector, SEZON):
            with op.batch_alter_table(SEZON) as batch_op:
                batch_op.create_foreign_key(
                    FK_AD, URUN, ["company_id", SUTUN], ["company_id", "id"]
                )

        # --- 4. Kapsayan indeks --------------------------------------------
        inspector = sa.inspect(bind)
        if INDEKS_AD not in _indeksler(inspector, SEZON):
            op.create_index(INDEKS_AD, SEZON, ["company_id", SUTUN])


def downgrade() -> None:
    bind = op.get_bind()

    with _YenidenKurulumKapisi(bind):
        inspector = sa.inspect(bind)
        if INDEKS_AD in _indeksler(inspector, SEZON):
            op.drop_index(INDEKS_AD, table_name=SEZON)

        inspector = sa.inspect(bind)
        if SUTUN in _sutunlar(inspector, SEZON):
            with op.batch_alter_table(SEZON) as batch_op:
                if FK_AD in _yabanci_anahtarlar(inspector, SEZON):
                    batch_op.drop_constraint(FK_AD, type_="foreignkey")
                batch_op.drop_column(SUTUN)

        inspector = sa.inspect(bind)
        if UQ_AD in _tekiller(inspector, URUN):
            with op.batch_alter_table(URUN) as batch_op:
                batch_op.drop_constraint(UQ_AD, type_="unique")
