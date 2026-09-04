"""Partinin ve SON KULLANMA TARİHİNİN deposu. ÇAĞIRANI YOKTUR.

Konu: PARTİ + SKT + FEFO, PR 1. Bu göçün açtığı tabloyu ve sütunu BU PR'DA
OKUYAN ya da YAZAN YOKTUR — seçici (`app/parti.py`) de çağrılmıyor. 0066'nın
duruşunun aynısı: şema ve SIRALAMA, onları unutacak bir çağıran ortaya
çıkmadan ÖNCE çivileniyor.

--- ÖLÇÜLEN KUSUR ----------------------------------------------------------

Stok bugün TEK BİR SAYIDIR: `products.stock NUMERIC(18,4)`. O sayı "elde 50
var" der ve BAŞKA HİÇBİR ŞEY SÖYLEMEZ. Özellikle şunu söylemez: o 50'nin
hangi kısmı hangi PARTİDEN geldi ve o partilerin son kullanma tarihi NEDİR.

`stock_movements` de söylemez: satırda `quantity` vardır, parti YOKTUR.

Bu, ilaç/gıda/tohum/BKÜ taşıyan bir stok için ÖLÇÜLEBİLİR bir kusurdur ve
kusurun iki ayrı yüzü var:

1. SÜRESİ GEÇMİŞ MAL SESSİZCE SATILIR. 50 adedin 10'u dün son kullanma
   tarihini geçtiyse bugünkü çıkış onu ayırt EDEMEZ, çünkü ayırt edecek veri
   KAYITTA YOKTUR. Ayırt edilemeyen bir şey reddedilemez de.
2. GERİ ÇAĞIRMA (recall) YAPILAMAZ. Üretici bir parti numarasını geri
   çağırdığında "o partiden kimlere ne gitti" sorusunun cevabı bu depoda
   HİÇBİR YERDE yoktur; cevap ancak parti hareketin ÜZERİNDE durursa vardır.

Bugün bu iki kusur GÖRÜNMEZDİR çünkü kimse parti sormuyor. Soran ilk gün
görünür olur ve o gün GEÇMİŞ VERİ YOKTUR — bu yüzden depo, sorudan ÖNCE
açılıyor.

--- `expiry_date` NULL KABUL EDER, VE BU MERKEZÎ KARARDIR ------------------

Sahip kararı 2: SKT İSTEĞE BAĞLIDIR.

Gerekçe ölçülebilir: bu depodaki ürünlerin ÇOĞUNUN son kullanma tarihi YOKTUR
(yedek parça, makine, alet). Bir cıvatanın SKT'si yoktur ve olmaması EKSİK
veri DEĞİLDİR — o cıvatanın DOĞRU verisidir.

`NOT NULL` yapmak iki kapıdan birine zorlardı ve ikisi de kötüdür:

  * Uzak bir tarih uydurmak (`9999-12-31`): ÖLÇÜLMEMİŞ bir olguyu ÖLÇÜLMÜŞ
    gibi kaydeder. 0066'nın `base_unit = unit` kopyalamasını reddetme
    gerekçesinin AYNISI.
  * SKT'siz ürünlere parti açtırmamak: geri çağırma ihtiyacını SKT'ye
    bağlardı, oysa ikisi AYRI olgudur — bir cıvata partisi de geri
    çağrılabilir.

NULL burada "bu partinin son kullanma tarihi YOKTUR" der. "Bilinmiyor" demez;
o ayrım bu PR'da AÇILMADI ve açılmaması bilinçlidir — iki anlamı ayırmak
İKİNCİ bir sütun ister (`skt_bilinmiyor BOOLEAN`) ve o sütunu bugün kimse
dolduramaz, çünkü doldurulacak GEÇMİŞ VERİ yoktur. Ayrım gerektiği gün,
gerçek veriyle açılmalıdır.

BU KARARIN BEDELİ SIRALAMAYA DÜŞER ve orada adı konmuştur: FEFO, "en erken
SKT önce" demektir ve SKT'si OLMAYAN bir parti bu sıraya doğal olarak
girmez. Seçici onu EN SONA koyar (`app/parti.py`, NULL-son kuralı). Sebep:
tarihi olan bir parti BOZULABİLİR, olmayan bozulmaz — bozulabilen önce
çıkmalıdır. Bunun tersi (NULL'u başa koymak) bozulabilir malı rafta
BEKLETİRDİ ve tam da bu göçün düzeltmek için var olduğu kusuru üretirdi.

--- `UNIQUE(company_id, product_id, lot_code)` -----------------------------

Bir firmanın bir ürünü için "A-2026-01" TEK BİR partidir. İki satır olsaydı
miktar İKİYE bölünür ve hangisinin gerçek olduğu SORULAMAZDI.

`lot_code` SERBEST METİNDİR (`TEXT`, `NOT NULL`) ve kapalı bir liste YOKTUR:
parti numarasını ÜRETİCİ basar, bu depo değil. Bir biçim dayatmak, gerçek
etiketleri reddetmek olurdu.

BARKOD AYRIŞTIRILMIYOR VE BU BİLİNÇLİ: GS1'de GTIN/EAN-13 ÜRÜNÜ tanımlar,
ÖRNEĞİ (parti/SKT) DEĞİL. Parti `AI(10)`, SKT `AI(17)`'dir ve YALNIZ
GS1-128 / DataMatrix taşıyıcılarında bulunur — EAN-13'te BULUNMAZ. Yani
mevcut `products.barcode` alanından parti ÇIKARILAMAZ. Bu PR barkod
ayrıştırmıyor; `lot_code` ve `expiry_date` GİRİLDİĞİ GİBİ saklanıyor.
Ayrıştırma yazmak, ölçülmemiş bir taşıyıcı varsayımı olurdu.

--- BİLEŞİK YABANCI ANAHTAR, ÇIPLAK OLANI DEĞİL (0062'nin KURALI) ---------

`(company_id, product_id) -> products(company_id, id)`, 0066'daki
`product_unit_factors` ile BİREBİR aynı gerekçe: çıplak
`product_id -> products.id` bir kiracının partisinin BAŞKA kiracının ürününü
işaret etmesini ENGELLEMEZ.

`stock_movements.lot_id` DE BİLEŞİK BAĞLANIR:
`(company_id, lot_id) -> product_lots(company_id, id)`. Bunun için
`product_lots` üzerinde `UNIQUE(company_id, id)` açılıyor — hedefi tekil
OLMAYAN bir bileşik anahtar kurulamaz.

`lot_id` NULL KABUL EDER ve NULL iken kısıt DENETLENMEZ (`MATCH SIMPLE`,
her iki diyalektin de varsayılanı). Bu tam olarak İSTENEN davranıştır:
partisi olmayan hareket geçerli bir harekettir ve mevcut milyonlarca satırın
hepsi öyledir.

--- `CHECK (quantity >= 0 AND quantity <> 'NaN'::numeric)` -----------------

`>= 0` ÇÜNKÜ `> 0` DEĞİL: TÜKENMİŞ bir parti (`quantity = 0`) SİLİNMEZ. O
satır geri çağırmanın kanıtıdır — "bu partiden mal GİRDİ ve BİTTİ" cümlesi,
satır silinirse "bu parti HİÇ OLMADI"ya dönüşür. `> 0` kısıtı tükenen partiyi
silmeye ya da negatife kaçmaya zorlardı.

Negatif ise kısıtlanır: bir partide EKSİ mal olamaz. Eksi bir parti miktarı,
çıkış yolunun partiden fazlasını düşürdüğünün kanıtıdır ve bu bir UYGULAMA
hatasıdır — ama veriye YAZILABİLİR olduğu için kısıt veritabanındadır.

`AND quantity <> 'NaN'::numeric` YARISININ GEREKÇESİ 0066'DA ÖLÇÜLDÜ ve
burada TEKRAR EDİLMEZ, ORAYA BAKILIR: PostgreSQL `NaN`ı her sonlu sayının
ÜSTÜNE sıralar, yani yalın `quantity >= 0` NaN'ı KABUL EDER. `<>` kullanılır
çünkü PostgreSQL'de `NaN = NaN` TRUE'dur. 0066 bu dersi PAHALIYA öğrendi
(kısıt bir tur boyunca belgelendiği şeyi savunmuyordu); burada ilk seferde
doğru yazılıyor ve `test_parti_skt_postgresql.py` onu MUTASYONLA çiviliyor.

NaN YARISI YALNIZ PostgreSQL'E EKLENİR (`_miktar_kisit_metni`, 0066'nın
`_katsayi_kisit_metni`sinin birebir eşi). `'NaN'::numeric` PostgreSQL
sözdizimidir ve SQLite göçü `unrecognized token: ":"` ile düşürür — 0066'da
ÖLÇÜLDÜ. Savunduğu kusur da yalnız orada.

--- `products.stock` İLE `SUM(product_lots.quantity)` AYRIŞABİLİR ---------

VE BU PR BUNU DÜZELTMİYOR. Adı konuyor:

Parti miktarları BU PR'DA HİÇBİR YERDE GÜNCELLENMİYOR — tüketim yolu YOKTUR
(sahip kararı 3). Yani bir firma parti satırları açıp `products.stock`u
başka bir yoldan değiştirirse iki sayı AYRIŞIR ve HİÇBİR ŞEY ŞİKÂYET ETMEZ.

TUTARLILIK KISITI EKLENMEDİ VE BU BİR KARARDIR, UNUTMA DEĞİL:

  * Bugün eklenirse MEVCUT her ürünü bozardı: bugün hiçbir ürünün partisi
    yoktur, yani `SUM(lots) = 0 <> stock` HER SATIR için doğrudur.
  * Kısıt ancak partiler stoğun TAMAMINI kapsadığında anlamlıdır ve o gün
    bağlama (wiring) PR'ının içindedir.

Bu YOKLUK `test_parti_skt_postgresql.py` içinde ADIYLA ÇİVİLENDİ
(`test_stok_ile_parti_toplami_AYRISABILIR_ikinci_katman_YOK`), 0066'nın
`test_products_stock_NaN_KABUL_EDER_ikinci_katman_YOK` testiyle aynı
gerekçeyle: bir gün kısıt eklenirse O TEST KIRMIZI OLUR ve bu DOĞRUDUR —
o gün orası, kısıtı doğrulayan teste döner. Çivilenmemiş bir delik sessizce
unutulur; çivilenmiş delik bir KARARDIR.

--- `core_schema.py` DEĞİŞTİRİLMEDİ ---------------------------------------

0066'nın gerekçesinin AYNISI: 0015 sonrası her sütun yalnız alembic'te yaşar.
Bildirmek, sayısal manifestoda bir VARLIK FARKI üretir ve sayısal göç
mutabakat kapısını kırar. `inventory.py` ve `numeric_manifest.py` de
DEĞİŞTİRİLMEDİ.

--- GERİ ALMA ---------------------------------------------------------------

`downgrade` önce `stock_movements.lot_id`i, sonra tabloyu düşürür — SIRA
ZORUNLUDUR, çünkü sütun tabloya yabancı anahtarla bağlıdır.

VERİ KAYBI VARDIR ve adı konmuştur: bütün parti satırları ve hareketlerin
parti bağı silinir. Bu PR'da hiçbir şey onları okumadığı için geri alma
davranışsal olarak GÖRÜNMEZDİR — 0066'daki davranışa dönülür.

Revision ID: 20260903_0067
Revises: 20260902_0066
"""
from alembic import op
import sqlalchemy as sa

revision = "20260903_0067"
down_revision = "20260902_0066"
branch_labels = None
depends_on = None

URUN = "products"
HAREKET = "stock_movements"
PARTI = "product_lots"

UQ_URUN = "uq_products_company_id"
UQ_PARTI_KIMLIK = "uq_product_lots_company_id"
UQ_PARTI_KODU = "uq_product_lots_company_product_code"
FK_PARTI_URUN = "fk_product_lots_product_same_company"
FK_HAREKET_PARTI = "fk_stock_movements_lot_same_company"
CK_PARTI_MIKTAR = "ck_product_lots_quantity_non_negative"
INDEKS_PARTI = "ix_product_lots_company_product"

# Parti miktarı ürünle AYNI ölçekte: o bir MİKTARDIR ve `products.stock` ile
# `stock_movements.quantity` ile aynı sütun ailesindendir (`core_schema.QUANTITY`).
MIKTAR_TIPI = sa.Numeric(18, 4)


def _sutunlar(inspector, tablo: str) -> set[str]:
    return {sutun["name"] for sutun in inspector.get_columns(tablo)}


def _tekiller(inspector, tablo: str) -> set[str]:
    return {item["name"] for item in inspector.get_unique_constraints(tablo)}


def _miktar_kisit_metni(bind) -> str:
    """`CHECK` ifadesi DİYALEKTE BAĞLIDIR — 0066'nın `_katsayi_kisit_metni`si.

    `quantity >= 0` her iki diyalektte de aynı şeyi söyler. NaN yarısı
    SÖYLEMEZ: `'NaN'::numeric` PostgreSQL sözdizimidir ve SQLite onu
    AYRIŞTIRAMAZ — 0066'da ÖLÇÜLDÜ, göç `sqlite3.OperationalError:
    unrecognized token: ":"` ile düşüyordu.

    Şart YALNIZ PostgreSQL'e eklenir çünkü savunduğu kusur YALNIZ orada:
    PostgreSQL `numeric` NaN SAKLAYABİLİR ve onu her sonlu sayının ÜSTÜNE
    sıralar, yani yalın `quantity >= 0` NaN'ı KABUL EDER. SQLite'ın
    `NUMERIC`i ölçek/tür dayatmaz ve kısıt orada zaten BAŞKA bir şey ölçerdi;
    taşınabilir GÖRÜNSÜN diye ölçülmemiş bir ifade yazmak, 0066'nın
    düzelttiği hatanın aynısı olurdu.

    SEÇİCİ (`app/parti.py`) HER İKİ DİYALEKTTE DE REDDEDER — yani SQLite'ta
    eksilen İKİNCİ katmandır, tek katman değil.
    """
    if bind.dialect.name == "postgresql":
        return "quantity >= 0 AND quantity <> 'NaN'::numeric"
    return "quantity >= 0"


def upgrade() -> None:
    bind = op.get_bind()

    # --- 1. Bileşik yabancı anahtarın hedefi tekil OLMALI (0062'nin kuralı) --
    # Taze kurulumda `core_schema`dan, mevcut veritabanlarında 0062/0066'dan
    # gelir; ikisi de yoksa burada kurulur. VARSAYILMAZ.
    inspector = sa.inspect(bind)
    if UQ_URUN not in _tekiller(inspector, URUN):
        with op.batch_alter_table(URUN) as batch:
            batch.create_unique_constraint(UQ_URUN, ["company_id", "id"])

    # --- 2. Parti defteri ----------------------------------------------------
    inspector = sa.inspect(bind)
    if PARTI not in set(inspector.get_table_names()):
        op.create_table(
            PARTI,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("product_id", sa.Integer(), nullable=False),
            # SERBEST METİN: parti numarasını ÜRETİCİ basar, bu depo değil.
            # Bir biçim dayatmak gerçek etiketleri reddetmek olurdu.
            sa.Column("lot_code", sa.Text(), nullable=False),
            # NULL = "bu partinin son kullanma tarihi YOKTUR" (sahip kararı 2).
            # "Bilinmiyor" DEMEZ; o ayrım ikinci bir sütun ister ve bugün onu
            # dolduracak geçmiş veri yoktur. Bkz. başlık.
            sa.Column("expiry_date", sa.Date(), nullable=True),
            sa.Column(
                "quantity", MIKTAR_TIPI, nullable=False, server_default="0"
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            # `>= 0`, `> 0` DEĞİL: tükenmiş parti SİLİNMEZ, o satır geri
            # çağırmanın kanıtıdır. NaN yarısı yalnız PostgreSQL'de — bkz.
            # `_miktar_kisit_metni` ve 0066'nın ölçümü.
            sa.CheckConstraint(_miktar_kisit_metni(bind), name=CK_PARTI_MIKTAR),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            # ÇIPLAK DEĞİL BİLEŞİK: çapraz kiracı ürün referansını engeller.
            sa.ForeignKeyConstraint(
                ["company_id", "product_id"],
                [f"{URUN}.company_id", f"{URUN}.id"],
                name=FK_PARTI_URUN,
            ),
            # Bir firmanın bir ürünü için bir parti kodu TEK satırdır; iki
            # satır miktarı ikiye böler ve hangisinin gerçek olduğu sorulamaz.
            sa.UniqueConstraint(
                "company_id", "product_id", "lot_code", name=UQ_PARTI_KODU
            ),
            # `stock_movements.lot_id`in bileşik yabancı anahtarının HEDEFİ.
            # Hedefi tekil olmayan bir bileşik anahtar KURULAMAZ.
            sa.UniqueConstraint("company_id", "id", name=UQ_PARTI_KIMLIK),
        )
        op.create_index(INDEKS_PARTI, PARTI, ["company_id", "product_id"])

    # --- 3. Hareketin parti bağı: NULL, geriye doldurma YOK ------------------
    # Mevcut hareketlerin hangi partiden çıktığı BİLİNMİYOR ve bilinemez —
    # ölçülen kusur zaten budur. NULL "bu hareket parti öncesindendir" der ve
    # bu DOĞRUDUR.
    inspector = sa.inspect(bind)
    if "lot_id" not in _sutunlar(inspector, HAREKET):
        with op.batch_alter_table(HAREKET) as batch:
            batch.add_column(sa.Column("lot_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                FK_HAREKET_PARTI,
                PARTI,
                ["company_id", "lot_id"],
                ["company_id", "id"],
            )


def downgrade() -> None:
    bind = op.get_bind()

    # SIRA ZORUNLUDUR: sütun tabloya yabancı anahtarla bağlı, önce O düşer.
    inspector = sa.inspect(bind)
    if "lot_id" in _sutunlar(inspector, HAREKET):
        with op.batch_alter_table(HAREKET) as batch:
            batch.drop_column("lot_id")

    inspector = sa.inspect(bind)
    if PARTI in set(inspector.get_table_names()):
        op.drop_table(PARTI)

    # `uq_products_company_id` DÜŞÜRÜLMEZ: onu bu göç YARATMIŞ OLMAYABİLİR
    # (0062, 0066 ya da `core_schema` koymuş olabilir) ve düşürmek onların
    # kendi yabancı anahtarlarının hedefini ELİNDEN ALIRDI. 0066'nın kararı.
