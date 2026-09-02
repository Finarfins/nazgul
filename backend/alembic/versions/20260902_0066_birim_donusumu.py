"""Birim dönüşümünün DEPOSU: taban birim, katsayı defteri, hareket kanıtı.

Konu: BİRİM DÖNÜŞÜMÜ, PR 1. Bu göçün açtığı hiçbir sütunu BU PR'DA OKUYAN
ya da YAZAN YOKTUR — çözücü (`app/units.py`) de çağrılmıyor. Şema ve ölçek,
onları unutacak bir çağıran ortaya çıkmadan ÖNCE çivileniyor.

--- ÖLÇÜLEN KUSUR ----------------------------------------------------------

`products.unit` (`VARCHAR(40)`, `server_default='Adet'`) SERBEST METİNDİR ve
stok sayısının HANGİ birimde olduğunu söylemez — yalnız EKRANDA ne yazacağını
söyler. `stock_movements.quantity` bir sayı taşır ve o sayının birimi hiçbir
yerde YAZILI DEĞİLDİR; okuyanın ürünün `unit` alanına bakıp aynı olduğunu
VARSAYMASI gerekir.

Bu varsayım bugün doğru, çünkü bugün dönüşüm YOK: her şey aynı birimde
girilir. Dönüşüm eklendiği AN yanlış olur ve yanlışlığı GÖRÜNMEZ olur —
kayıtta "50" yazar, neyin 50'si olduğu kaybolmuştur.

--- ÜÇ SÜTUN KÜMESİ, ÜÇ AYRI OLGU -----------------------------------------

``products.base_unit``        Ürünün stoğunun TUTULDUĞU birim.
``product_unit_factors``      Firmanın "1 X = N taban" BEYANLARI, EKLEMELİ.
``stock_movements.entered_*`` Hareketin O GÜNKÜ girdisi ve KULLANILAN katsayı.

Üçü ayrı, çünkü üç ayrı soruya cevap veriyorlar ve tek sütunda birleşmeleri
0063'ün ve 0065'in kaçındığı hatanın aynısı olurdu: birleşik bir sütunun BOŞ
olması iki şeyi birden söylerdi.

--- `base_unit` NULL KABUL EDER VE GERİYE DOLDURULMAZ ---------------------

`server_default` YOKTUR ve geriye doldurma YOKTUR. Bu, 0062'nin ve 0063'ün
duruşunun aynısıdır ve BURADA ÖZELLİKLE ÖNEMLİDİR.

`products.unit`ten kopyalamak (`base_unit = unit`) cazip ve YANLIŞ olurdu:
`unit` bir GÖRÜNTÜ etiketidir, kimse onu "stok bu birimde tutuluyor" diye
DOĞRULAMADI. Kopyalamak, doğrulanmamış bir etiketi DOĞRULANMIŞ bir olguya
terfi ettirirdi ve terfi kayıtta GÖRÜNMEZDİ — sonraki okuyucu `base_unit`i
firmanın beyanı sanardı. `'Adet'` varsayılanı da aynı kapıya çıkardı: bir
şeyin adet olduğu ÖLÇÜLMÜŞ değil VARSAYILMIŞ olurdu.

NULL burada "bu firma tabanını HENÜZ BİLDİRMEDİ" demektir ve bu, EKSİK veri
değil, DÜRÜST veridir. Sahip kararı 2 bunun sonucunu da bağladı: tabanı NULL
olan bir üründe taban dışı birim girilirse çözücü REDDEDER (outbox tarafında
adı konmuş bir atlama kovası, etkileşimli yazmada ret). Bu PR yalnız reddin
TEMİZ dönmesini istiyor; ısırması PR 2'den itibarendir.

--- `product_unit_factors` EKLEMELİDİR: `UPDATE` YOKTUR -------------------

Sahip kararı 1: yanlış çıkan bir katsayı ASLA yeniden hesaplanmaz ve satır
ASLA güncellenmez. Düzeltme, `effective_from`u daha yeni olan YENİ BİR
SATIRDIR.

Gerekçe iki tanedir ve ikisi de geri alınamaz olanı koruyor:

1. Geçmişi yeniden hesaplamak, MUTABAKATI YAPILMIŞ faturaları geçersiz
   kılardı. Bir fatura kesildiğinde o günkü katsayıyla kesildi.
2. `UPDATE`, 50'nin BİR ZAMANLAR doğru sanıldığına dair TEK KANITI silerdi.
   Denetimin sorusu "doğru sayı neydi" değil, "o gün neye inanıldı"dır.

Fiziksel stok gerçekten yanlışsa çare TARİHLİ BİR STOK DÜZELTME HAREKETİDİR
ve fark CARİ DÖNEME düşer. Sahip bunu açıkça kabul etti.

Bu göç `UPDATE`i şemayla YASAKLAMIYOR — bir tetikleyici ya da `REVOKE`
eklenmedi — çünkü kural UYGULAMA katmanının sözleşmesidir ve tetikleyici
eklemek bu deponun hiçbir yerinde kurulmamış bir desen olurdu. Yasak
BURADA YAZILI ve `effective_from`un varlığıyla ŞEMADA GÖRÜNÜR: tek bir
geçerli satır olsaydı o sütun gereksiz olurdu.

--- `CHECK factor > 0` ------------------------------------------------------

Sıfır katsayı her miktarı sessizce 0'a çevirirdi; negatif katsayı bir girişi
ÇIKIŞA çevirirdi. İkisi de UYGULAMA hatasıdır ama ikisi de veriye YAZILABİLİR
olduğu için kısıt veritabanındadır. Çözücü aynı denetimi ayrıca yapıyor
(`KATSAYI_GECERSIZ`); iki katman KASITLIDIR — çözücü çağrılmadan yazılan bir
satırı yalnız veritabanı yakalar.

--- BİLEŞİK YABANCI ANAHTAR, ÇIPLAK OLANI DEĞİL (0062'nin KURALI) ---------

`(company_id, product_id) -> products(company_id, id)`. Çıplak
`product_id -> products.id` bir kiracının katsayı beyanının BAŞKA kiracının
ürününü işaret etmesini ENGELLEMEZ. Hedefteki `uq_products_company_id`
0062'den (mevcut veritabanları) ya da `core_schema`dan (taze kurulum) gelir;
bu göç onu VARSAYMAZ, yoksa kurar.

--- `stock_movements`: ÜÇÜ DE NULL, GERİYE DOLDURMA YOK ------------------

`entered_quantity`, `entered_unit`, `entered_factor`. Üçü de NULL kabul eder
ve mevcut satırlara HİÇBİR ŞEY yazılmaz.

Mevcut hareketler için doğru değer BİLİNMİYOR ve bilinemez: bugünkü `quantity`
hangi birimde girildiyse o birimde girildi, ama o birimin ne olduğu KAYITTA
YOKTUR (ölçülen kusur zaten budur). `entered_unit = products.unit` yazmak,
kaybolmuş bir olguyu UYDURMAK olurdu ve uydurma KAYITTA "ölçüldü" gibi
görünürdü. NULL "bu hareket dönüşüm öncesindendir" der ve bu DOĞRUDUR.

`entered_factor` NUMERIC(24,10)'dur, ürünün NUMERIC(18,4)'ünden ÇOK daha
geniş. Kasıtlıdır: katsayı YUVARLANMADAN saklanmalı ki "o gün neye inanıldı"
kanıtı bozulmasın. `entered_quantity` ise ürünün ölçeğindedir (18,4) çünkü o
bir MİKTARDIR, katsayı değil.

--- `core_schema.py` DEĞİŞTİRİLMEDİ ---------------------------------------

Bu sütunların HİÇBİRİ `core_schema.py`ye BİLDİRİLMEDİ ve bu, orada
`reorder_point` için yazılmış NOT'un aynı gerekçesidir: 0015 sonrası her
sütun yalnız alembic'te yaşar ve ham `text()` ile okunur. Bildirmek onları
sayısal manifestoya kaydettirirdi; manifestonun sözleşmesi "0000 tabanında
mevcut"tur, sonradan eklenen bir sütun orada bir VARLIK FARKI olarak görünür
ve sayısal göç mutabakat kapısını kırar.

`inventory.py` ve `numeric_manifest.py` de DEĞİŞTİRİLMEDİ. `post_hardening`
düzenlenmesi gerekiyorsa tasarım kaymış demektir.

--- GERİ ALMA ---------------------------------------------------------------

`downgrade` üç sütun kümesini ve tabloyu düşürür. VERİ KAYBI VARDIR ve adı
konmuştur: firmanın taban bildirimleri ile katsayı defterinin TAMAMI silinir.
Bu PR'da hiçbir şey onları okumadığı için geri alma davranışsal olarak
GÖRÜNMEZDİR — 0065'teki davranışa dönülür.

Revision ID: 20260902_0066
Revises: 20260902_0065
"""
from alembic import op
import sqlalchemy as sa

revision = "20260902_0066"
down_revision = "20260902_0065"
branch_labels = None
depends_on = None

URUN = "products"
HAREKET = "stock_movements"
KATSAYI = "product_unit_factors"

UQ_URUN = "uq_products_company_id"
FK_KATSAYI_URUN = "fk_product_unit_factors_product_same_company"
CK_KATSAYI_POZITIF = "ck_product_unit_factors_factor_positive"
UQ_KATSAYI_SATIR = "uq_product_unit_factors_effective"
INDEKS_KATSAYI = "ix_product_unit_factors_company_product"

# Katsayı ürünün 4 basamağından ÇOK daha geniş: kanıt yuvarlanmamalı.
KATSAYI_TIPI = sa.Numeric(24, 10)
# Miktar ürünle AYNI ölçekte: o bir miktardır, katsayı değil.
MIKTAR_TIPI = sa.Numeric(18, 4)

HAREKET_SUTUNLARI = ("entered_quantity", "entered_unit", "entered_factor")


def _sutunlar(inspector, tablo: str) -> set[str]:
    return {sutun["name"] for sutun in inspector.get_columns(tablo)}


def _tekiller(inspector, tablo: str) -> set[str]:
    return {item["name"] for item in inspector.get_unique_constraints(tablo)}


def upgrade() -> None:
    bind = op.get_bind()

    # --- 1. products.base_unit: NULL, server_default YOK, geriye doldurma YOK
    inspector = sa.inspect(bind)
    if "base_unit" not in _sutunlar(inspector, URUN):
        op.add_column(URUN, sa.Column("base_unit", sa.String(length=40), nullable=True))

    # --- 2. Bileşik yabancı anahtarın hedefi tekil OLMALI (0062'nin kuralı)
    # Taze kurulumda `core_schema`dan, mevcut veritabanlarında 0062'den gelir;
    # ikisi de yoksa burada kurulur. VARSAYILMAZ.
    inspector = sa.inspect(bind)
    if UQ_URUN not in _tekiller(inspector, URUN):
        with op.batch_alter_table(URUN) as batch:
            batch.create_unique_constraint(UQ_URUN, ["company_id", "id"])

    # --- 3. Katsayı defteri: EKLEMELİ ---------------------------------------
    inspector = sa.inspect(bind)
    if KATSAYI not in set(inspector.get_table_names()):
        op.create_table(
            KATSAYI,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("product_id", sa.Integer(), nullable=False),
            # SERBEST METİN — kapalı liste GEREKMEZ, çünkü çözücü zaten ne
            # evrensel ne de burada beyan edilmiş olan her şeyi reddediyor.
            # Karşılaştırma Türkçe katlanır (`app/units.turkce_katla`).
            sa.Column("unit_code", sa.String(length=40), nullable=False),
            # "1 <unit_code> = <factor> <base_unit>".
            sa.Column("factor", KATSAYI_TIPI, nullable=False),
            # EKLEMELİ defterin ekseni: düzeltme YENİ SATIRDIR, `UPDATE` değil.
            sa.Column("effective_from", sa.Date(), nullable=False),
            # Beyanın gerekçesi; düzeltme satırının NEDEN yazıldığı burada.
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            # Sıfır her miktarı sessizce 0 yapardı; negatif girişi ÇIKIŞA
            # çevirirdi. İkisi de yazılabilir olduğu için kısıt buradadır.
            sa.CheckConstraint("factor > 0", name=CK_KATSAYI_POZITIF),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            # ÇIPLAK DEĞİL BİLEŞİK: çapraz kiracı ürün referansını engeller.
            sa.ForeignKeyConstraint(
                ["company_id", "product_id"],
                [f"{URUN}.company_id", f"{URUN}.id"],
                name=FK_KATSAYI_URUN,
            ),
            # AYNI ürün + AYNI birim + AYNI gün için İKİ satır olamaz: o gün
            # hangisinin geçerli olduğu belirsiz kalırdı. Farklı GÜNLER
            # serbesttir — defterin EKLEMELİ olması tam olarak budur.
            sa.UniqueConstraint(
                "company_id",
                "product_id",
                "unit_code",
                "effective_from",
                name=UQ_KATSAYI_SATIR,
            ),
        )
        op.create_index(INDEKS_KATSAYI, KATSAYI, ["company_id", "product_id"])

    # --- 4. Hareketin kanıt sütunları: ÜÇÜ DE NULL, geriye doldurma YOK -----
    inspector = sa.inspect(bind)
    mevcut = _sutunlar(inspector, HAREKET)
    if not set(HAREKET_SUTUNLARI).issubset(mevcut):
        with op.batch_alter_table(HAREKET) as batch:
            if "entered_quantity" not in mevcut:
                batch.add_column(
                    sa.Column("entered_quantity", MIKTAR_TIPI, nullable=True)
                )
            if "entered_unit" not in mevcut:
                batch.add_column(
                    sa.Column("entered_unit", sa.String(length=40), nullable=True)
                )
            if "entered_factor" not in mevcut:
                batch.add_column(
                    sa.Column("entered_factor", KATSAYI_TIPI, nullable=True)
                )


def downgrade() -> None:
    bind = op.get_bind()

    inspector = sa.inspect(bind)
    mevcut = _sutunlar(inspector, HAREKET)
    if set(HAREKET_SUTUNLARI) & mevcut:
        with op.batch_alter_table(HAREKET) as batch:
            for sutun in HAREKET_SUTUNLARI:
                if sutun in mevcut:
                    batch.drop_column(sutun)

    inspector = sa.inspect(bind)
    if KATSAYI in set(inspector.get_table_names()):
        op.drop_table(KATSAYI)

    # `uq_products_company_id` DÜŞÜRÜLMEZ: onu bu göç YARATMIŞ OLMAYABİLİR
    # (0062 ya da `core_schema` koymuş olabilir) ve düşürmek 0062'nin kendi
    # yabancı anahtarının hedefini ELİNDEN ALIRDI.
    inspector = sa.inspect(bind)
    if "base_unit" in _sutunlar(inspector, URUN):
        op.drop_column(URUN, "base_unit")
