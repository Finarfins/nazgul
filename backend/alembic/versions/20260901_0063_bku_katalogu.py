"""BKÜ kataloğu: PHI gün sayısının ETİKETTEN gelen kaydı.

Revision ID: 20260901_0063
Revises: 20260827_0062

--- ÖLÇÜLEN KUSUR ------------------------------------------------------------

PHI kilidi (`_hasat_guvenlik_dogrula`, göç 0046/0048) ÇALIŞIYOR. Beslendiği
sayı çalışmıyor: `field_activities.preharvest_interval_days` ELLE, faaliyet
başına yazılıyor. Deponun tamamında ölçüldü — hiçbir yerde bir BKÜ kataloğu
ya da ürün başına PHI değeri YOK.

Sonuç: operatör sayıyı yazmayı unuttuğunda kilit SESSİZCE hiçbir şey yapmaz.
0044'ten beri geçerli olan kural (`_bekleme_ihlalleri`) boş süreyi ihlal
SAYMIYOR ve bu göç o kuralı DEĞİŞTİRMİYOR — bilinmeyeni ihlal saymak
kullanıcıyı gerekçe yazmaya alıştırır, gerçek uyarıyı değersizleştirir. Bu
göç boşun ANLAMINI değil, boş kalma SIKLIĞINI düşürüyor.

Yol haritasının kendi kuralı zaten bunu istiyordu: "PHI gün sayısı koda
gömülmez — etiketten/parametreden gelir."

--- DEPO HİÇBİR PHI RAKAMI İDDİA ETMEZ --------------------------------------

Bu göç TEK BİR SATIR VERİ YAZMAZ. Başlangıç listesi, paketlenmiş bakanlık
verisi, ürün başına varsayılan YOK. Sistemdeki her PHI değerini o değerin
sahibi firma girmiştir.

Bu bir kolaylık tercihi değil, HUKUKİ bir duruş: PHI yasal bir süredir ve
kaynağı BKÜ etiketidir. Depoya gömülen bir rakam, yanlış olduğunda deponun
iddiası olur. Kataloğu firma doldurur; depo yalnız saklar ve hatırlatır.

--- ŞEMA: ÜRÜN BAŞINA DEĞİL, ÜRÜN + BİTKİ BAŞINA ---------------------------

PHI ürünün TEK BAŞINA özelliği DEĞİLDİR: aynı etken madde domateste ve elmada
farklı bekleme süresi taşır. Ürün başına tek satır tutmak, alan gerçeğini
şemaya yanlış yazmak olurdu ve sonradan düzeltmek veri taşıma gerektirirdi.

Ama `crop_seasons.crop` SERBEST METİNDİR (`VARCHAR(120)`, 0044). Her ürün için
her bitkiye satır girmeyi ZORUNLU kılmak, kataloğu doldurulamaz hale getirir:
firma "Domates" yazarken sezonda "domates" yazılıysa eşleşme kaçar ve katalog
sessizce işlevsiz kalır.

Seçim: satır `(product_id, crop)` başına, `crop` BOŞ DİZE olabilir ve boş dize
"BÜTÜN BİTKİLER" demektir. Çözüm sırası bitkiye ÖZEL satırı tercih eder, yoksa
bitkiden BAĞIMSIZ satıra düşer. Yani şema ürün+bitkiyi tam olarak ifade
edebiliyor, kullanım ise tek satırla başlanmasına izin veriyor.

NEDEN NULL DEĞİL BOŞ DİZE. SQL'de UNIQUE, NULL'ları BİRBİRİNDEN FARKLI sayar;
`crop` NULL olsaydı aynı ürüne sınırsız sayıda "bütün bitkiler" satırı
girilebilir ve çözüm HANGİSİNİ seçeceğini bilemezdi. Boş dize ile
`UNIQUE(company_id, product_id, crop)` belirsizliği ŞEMA seviyesinde kapatıyor.

--- `product_id` NULL KABUL ETMEZ -------------------------------------------

Çözüm yolu `field_activity_inputs.product_id` -> katalog şeklinde işliyor.
Ürüne bağlı OLMAYAN bir katalog satırı hiçbir faaliyete bağlanamaz; yani
hiçbir şeyi çözemez. Böyle bir satıra izin vermek, kullanıcıya doldurduğu ama
hiç kullanılmayan bir alan vermek olurdu — bu göçün düzeltmeye çalıştığı
kusurun ta kendisi.

Serbest metin girdi (`input_name` var, `product_id` YOK) çözülmez ve
çözülmemeli: çiftçi kendi ürettiği gübreyi kaydedebilmeli ve bunun etiketi
yoktur. O faaliyet boş PHI ile kalır, boş da ihlal değildir. Zincir tutarlı.

--- BİLEŞİK YABANCI ANAHTAR -------------------------------------------------

`(company_id, product_id) -> products(company_id, id)`. Çıplak anahtar bir
kiracının katalog satırının BAŞKA kiracının ürününü işaret etmesini
engellemez. Hedef tekilliği `uq_products_company_id` ile 0062'de zaten
kurulmuştu; bu göç onu KULLANIYOR, yeniden kurmuyor.

--- FAALİYETTEKİ İKİ YENİ SÜTUN: KAYNAK KAYIT ALTINDA ----------------------

`preharvest_source`         — etkin değeri KİM koydu
`catalogue_preharvest_days` — katalog NE demişti

Bu ayrım 0048'in kurduğu desenin aynısı: `safety_override_reason` KULLANICININ
söylediği, `safety_warning` SİSTEMİN bulduğudur ve aynı sütunda tutmak
denetimde kimin ne dediğini ayırt edilemez kılardı. Burada da operatörün
değeri `preharvest_interval_days`te, katalogun değeri AYRI sütunda duruyor.

Böylece "operatör katalogdaki 21 günü 7 yaptı" denetimde GÖRÜNÜR bir olaydır.
Tek sütun olsaydı üstüne yazma SESSİZ olurdu.

--- GERİYE DOLDURMA YOK ----------------------------------------------------

Mevcut `field_activities` satırları katalogdan PHI DEVRALMAZ. İki sütun da
NULL açılıyor ve NULL "bu satır katalog çağından önce yazıldı" demektir.

Gerekçe: bugün temiz kaydedilen bir hasat yarın da temiz kaydedilmelidir.
Geriye doldurma, geçmiş faaliyetlere sonradan bir PHI takıp bugüne kadar
sorunsuz çalışan hasat kayıtlarını aniden ihlale düşürürdü — kullanıcının
yapmadığı bir değişiklik yüzünden.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260901_0063"
down_revision = "20260827_0062"
branch_labels = None
depends_on = None

KATALOG = "plant_protection_products"
FAALIYET = "field_activities"
URUN = "products"


def _sutunlar(inspector, tablo: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(tablo)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if KATALOG not in set(inspector.get_table_names()):
        op.create_table(
            KATALOG,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            # Katalog satırı MUTLAKA bir stok ürününü tarif eder (başlıktaki
            # gerekçe): bağsız satır hiçbir faaliyeti çözemez.
            sa.Column("product_id", sa.Integer(), nullable=False),
            # BOŞ DİZE = bütün bitkiler. NULL DEĞİL — gerekçe başlıkta.
            sa.Column("crop", sa.String(length=120), nullable=False, server_default=""),
            # Ruhsat numarası etiketin kimliğidir; denetimde "bu süre nereden
            # geldi" sorusunun cevabı. Zorunlu değil: firma elindeki bilgiyle
            # başlayabilmeli, eksik ruhsat no kataloğu bloke etmemeli.
            sa.Column("registration_no", sa.String(length=60), nullable=True),
            # Kataloğun VAR OLMA SEBEBİ bu sütun; NOT NULL.
            sa.Column("preharvest_interval_days", sa.Integer(), nullable=False),
            # Tarlaya giriş yasağı da etiketten gelir ama kilidi yok; bilgi
            # olarak taşınıyor (`/field-safety` bunu faaliyetten okuyor).
            sa.Column("reentry_interval_days", sa.Integer(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column(
                "status", sa.String(length=20), nullable=False, server_default="ACTIVE"
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "preharvest_interval_days >= 0 AND preharvest_interval_days <= 3650",
                name="ck_ppp_preharvest_range",
            ),
            sa.CheckConstraint(
                "reentry_interval_days IS NULL OR "
                "(reentry_interval_days >= 0 AND reentry_interval_days <= 3650)",
                name="ck_ppp_reentry_range",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            # Çıplak değil BİLEŞİK: çapraz kiracı ürün referansını engeller.
            sa.ForeignKeyConstraint(
                ["company_id", "product_id"],
                [f"{URUN}.company_id", f"{URUN}.id"],
                name="fk_ppp_product_same_company",
            ),
            # Aynı ürün+bitki için İKİ satır olamaz; çözüm belirsiz kalmasın.
            sa.UniqueConstraint(
                "company_id", "product_id", "crop", name="uq_ppp_company_product_crop"
            ),
            # İleride bileşik yabancı anahtar hedefi olabilmesi için.
            sa.UniqueConstraint("company_id", "id", name="uq_ppp_company_id"),
        )
        op.create_index("ix_ppp_company_product", KATALOG, ["company_id", "product_id"])

    # --- faaliyetteki köken sütunları --------------------------------------
    inspector = sa.inspect(bind)
    mevcut = _sutunlar(inspector, FAALIYET)
    if "preharvest_source" not in mevcut or "catalogue_preharvest_days" not in mevcut:
        with op.batch_alter_table(FAALIYET) as batch:
            if "preharvest_source" not in mevcut:
                batch.add_column(
                    sa.Column("preharvest_source", sa.String(length=20), nullable=True)
                )
            if "catalogue_preharvest_days" not in mevcut:
                batch.add_column(
                    sa.Column("catalogue_preharvest_days", sa.Integer(), nullable=True)
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    mevcut = _sutunlar(inspector, FAALIYET)
    if "preharvest_source" in mevcut or "catalogue_preharvest_days" in mevcut:
        with op.batch_alter_table(FAALIYET) as batch:
            if "catalogue_preharvest_days" in mevcut:
                batch.drop_column("catalogue_preharvest_days")
            if "preharvest_source" in mevcut:
                batch.drop_column("preharvest_source")

    inspector = sa.inspect(bind)
    if KATALOG in set(inspector.get_table_names()):
        op.drop_index("ix_ppp_company_product", table_name=KATALOG)
        op.drop_table(KATALOG)
