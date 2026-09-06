"""Ekim-arası bekleme (plant-back) ve giriş yasağının KÖKENİ.

Revision ID: 20260907_0072
Revises: 20260906_0071

--- İKİ AYRI EKSİK, TEK GÖÇ -------------------------------------------------

Bu göç iki ölçülmüş boşluğu kapatıyor. İkisi de aynı zincirin halkası (BKÜ
etiketi -> katalog -> faaliyet -> kilit) olduğu için tek göçe alındı.

1. EKİM-ARASI BEKLEME (PLANT-BACK) ŞEMADA YOK. Devir notunun kendi cümlesi:
   "herbisit ekim-arası 10-18 ay". Bu bir PHI değildir ve bir giriş yasağı da
   değildir: ilacın toprakta kalan etkisi YENİ EKİLECEK bitkiyi yakar. Depo
   bugün bunu HİÇBİR YERDE tutmuyor — ölçüldü: `plant_protection_products`ta
   yalnız `preharvest_interval_days` ve `reentry_interval_days` var, ikisi de
   AYNI sezonun hasadına/girişine bakar, GELECEK sezonun ekimine değil.

2. GİRİŞ YASAĞININ KÖKENİ KAYITTA YOK. E1a (`_giris_yasagi_coz`) giriş
   yasağını katalogdan çözüyor ama hangi değerin KİMDEN geldiğini
   yazmıyor; o fonksiyonun kendi başlığı bunu ADIYLA söylüyordu:
   "KÖKEN SÜTUNU YOK — BİLEREK VE ÖLÇÜLEREK ... Onları açmak bir GÖÇTÜR ve
   bu dilim göçsüzdür". Bu göç o iki sütunu açıyor; PHI'de 0063'ün açtığı
   `preharvest_source` + `catalogue_preharvest_days` çiftinin BİREBİR eşi.

--- NEDEN SÜTUN DEĞİL AYRI TABLO (ÖLÇÜLDÜ, VARSAYILMADI) -------------------

İlk tasarım plant-back'i `plant_protection_products`a İKİ SÜTUN olarak
koyuyordu (`plantback_interval_days`, `plantback_crop`). ÖLÇÜLDÜ ve
GEÇMEDİ: o tablonun tekilliği `UNIQUE(company_id, product_id, crop)`tır
(göç 0063) ve plant-back'in ihtiyacı ÜRÜN + UYGULANDIĞI BİTKİ başına BİRDEN
ÇOK satırdır — çünkü aynı herbisit ardından ekilecek AYÇİÇEĞİ için 12 ay,
MERCİMEK için 4 ay bekletir. Ölçüm (CPython 3.12, SQLite 3.45):

    INSERT ... (1, 7, 'buğday', ..., 365, 'ayçiçeği')   -> GEÇTİ
    INSERT ... (1, 7, 'buğday', ..., 120, 'mercimek')   -> IntegrityError
        UNIQUE constraint failed: company_id, product_id, crop

Yani sütun tercihi ikinci satırı ŞEMA SEVİYESİNDE imkânsız kılıyordu ve
firma ikinci bitkiyi ancak birinciyi SİLEREK girebilirdi. 0063'ün UNIQUE'i
DEĞİŞTİRİLMEDİ: o kısıt PHI çözümünün belirsiz kalmamasını sağlıyor ve
gevşetmek bu göçün ihtiyacı için bir GÜVENLİK kuralını bozmak olurdu.

Seçim: AYRI TABLO `plant_protection_plantbacks`, tekilliği
`UNIQUE(company_id, product_id, crop, next_crop)`. Böylece ürün+bitki başına
ARDIL BİTKİ SAYISI KADAR satır girilebiliyor ve her satırın çözümü yine tek.

--- İKİ BOŞ DİZE, İKİ AYRI SORU --------------------------------------------

``crop``       — ilacın UYGULANDIĞI bitki.  '' = hangi bitkide atılırsa atılsın
``next_crop``  — ARDINDAN ekilecek bitki.   '' = ardından ne ekilirse ekilsin

NULL DEĞİL BOŞ DİZE, gerekçesi 0063'ünkiyle AYNI ve burada İKİ KAT geçerli:
SQL'de UNIQUE NULL'ları birbirinden farklı sayar, dolayısıyla NULL'lu bir
tekillik aynı ürüne sınırsız "her bitki için" satırı bırakırdı ve çözüm
hangisini seçeceğini bilemezdi.

Çözüm sırası da 0063'ün sırası: ÖZEL satır GENEL satıra tercih edilir, yani
(bitkiye özel, ardıla özel) > (bitkiye özel, ardıl '') > (bitki '', ardıla
özel) > (bitki '', ardıl ''). Uygulama tarafında birden çok satır aynı anda
eşleşirse EN UZUN süre kazanır (`routers/farm.py._plantback_ihlalleri`) —
kısa olanı seçmek, uzun olanın süresi dolmadan ekime izin verirdi.

--- ``interval_days`` NOT NULL --------------------------------------------

Bu tablonun VAR OLMA SEBEBİ o sayıdır. Süresi boş bir plant-back satırı
hiçbir şey söylemez ve hiçbir ekimi kesmez; doldurulup hiç kullanılmayan
bir alan olurdu — 0063'ün `preharvest_interval_days`i NOT NULL yapan
gerekçenin aynısı. Süre GÜN cinsindendir: devir notu "10-18 ay" diyor ama
takvim ayı sabit uzunlukta değildir ve mevcut iki kilit (`_bekleme_ihlalleri`,
`_giris_ihlalleri`) GÜN topluyor; ay birimi eklemek aynı tarih aritmetiğinin
İKİNCİ bir sürümünü doğururdu.

--- BİLEŞİK YABANCI ANAHTAR ------------------------------------------------

``(company_id, product_id) -> products(company_id, id)``, 0063'ün kalıbı.
Çıplak anahtar bir kiracının plant-back satırının BAŞKA kiracının ürününü
işaret etmesini engellemez. ``UNIQUE(company_id, id)`` ise 0063'teki gibi
İLERİDE bileşik hedef olabilmek için.

--- DEPO HİÇBİR SÜRE İDDİA ETMEZ -------------------------------------------

0063'ün duruşu BURADA DA GEÇERLİ ve tekrarlanıyor: bu göç TEK SATIR VERİ
YAZMAZ. Başlangıç listesi, paketlenmiş etiket verisi, ürün başına varsayılan
YOK. Plant-back süresi BKÜ etiketinden okunur ve onu firma girer. Kodda yasal
sabit YOKTUR.

--- GERİYE DOLDURMA YOK ----------------------------------------------------

`field_activities`in iki yeni sütunu NULL açılıyor ve NULL "bu satır köken
çağından önce yazıldı" demektir. Geçmiş faaliyetlere sonradan bir köken
uydurmak, 0063'ün reddettiği şeyin aynısı olurdu.

Aynı şekilde plant-back kilidi GEÇMİŞ ekimleri yeniden yargılamıyor: kilit
YALNIZ yeni bir sezon yazılırken (`POST /api/crop-seasons`) ya da mevcut
sezonun bitkisi/parseli/başlangıcı DEĞİŞİRKEN (`PUT`) işliyor.

--- ``farm_plantback_policy``: EN GEVŞEK SEVİYE "allow" DEĞİL -------------

0048'in erken hasat kuralıyla ve 0064'ün giriş yasağı kuralıyla AYNI sınır:
seviyeler ``block | require_reason | warn`` ve "allow" YOK. Plant-back
ihlali bir sonraki ürünü yakar; kontrolü tamamen kapatabilen bir ayar sessiz
bir güvenlik kapatma düğmesi olurdu. ``warn`` seviyesinde istek KABUL
EDİLİYOR ama sistemin bulduğu metin `crop_seasons.plantback_warning`e
YAZILIYOR.

Bu göç o üç seviyeyi CHECK ile ŞEMAYA da yazıyor — 0048 ve 0064 yazmamıştı
ve o eksiklik ölçülebilir: bugün `UPDATE companies SET farm_reentry_policy=
'kapali'` veritabanı tarafından KABUL EDİLİR, kapı yalnız uçtaki
`Literal[...]`dır. Yeni sütun o boşlukla doğmuyor. ESKİ İKİ SÜTUNA CHECK
EKLENMİYOR: mevcut satırlarda ölçülmemiş bir değer varsa göç PATLARDI ve bu
göçün konusu o değil.

--- ``plantback_warning`` ve ``plantback_override_reason`` AYRI SÜTUN ------

0048'in kuralı, üçüncü kez: birincisi SİSTEMİN bulduğu, ikincisi
KULLANICININ söylediği. Aynı sütunda tutmak, "warn" seviyesinde sistemin
ürettiği metni kullanıcının gerekçesi gibi gösterirdi.

İkisi de `Text`: 0064'ün `monoculture_override_reason`ı `String(255)`ti ve
o sınır uçtaki şemada ZATEN duruyor (`max_length=255`); sütunu `Text`
açmak diyalektler arasında aynı davranıyor ve gerekçe metni bir gün
uzarsa göç gerektirmiyor.

--- SQLite: CHECK'LER KENDİ BATCH'İNDE ------------------------------------

0071'in dersi: SQLite'ta var olan bir tabloya CHECK eklemek tabloyu YENİDEN
KURAR ve `downgrade`de yalnız `drop_column` çağırmak, yeniden kurulumun
YANSITTIĞI CHECK az önce düşürülen sütunu ADIYLA andığı için
`OperationalError` verir. Bu yüzden `companies`in CHECK'i ile sütunu AYNI
batch'te doğuyor ve AYNI batch'te ölüyor.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260907_0072"
down_revision = "20260906_0071"
branch_labels = None
depends_on = None

KATALOG = "plant_protection_products"
PLANTBACK = "plant_protection_plantbacks"
FAALIYET = "field_activities"
SEZON = "crop_seasons"
FIRMA = "companies"
URUN = "products"

#: 0063'ün `ck_ppp_preharvest_range` sınırıyla AYNI. Üç yer (şema, uç şeması,
#: içe aktarma) farklı sınır söyleseydi bir değer forma girilip veritabanına
#: yazılamaz olurdu.
EN_COK_GUN = 3650

#: 0048/0064'ün seviyeleri. "allow" YOK — gerekçe modül başlığında.
PLANTBACK_SEVIYELERI = ("block", "require_reason", "warn")


def _sutunlar(inspector, tablo: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(tablo)}


def _seviye_check() -> str:
    liste = ",".join("'%s'" % s for s in PLANTBACK_SEVIYELERI)
    return "farm_plantback_policy IN (%s)" % liste


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- (a) plant-back tablosu -------------------------------------------
    if PLANTBACK not in set(inspector.get_table_names()):
        op.create_table(
            PLANTBACK,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            # Bağsız satır hiçbir faaliyeti çözemez (0063'ün gerekçesi).
            sa.Column("product_id", sa.Integer(), nullable=False),
            # BOŞ DİZE = ilacın atıldığı bitki ne olursa olsun.
            sa.Column("crop", sa.String(length=120), nullable=False, server_default=""),
            # BOŞ DİZE = ardından ne ekilirse ekilsin.
            sa.Column(
                "next_crop", sa.String(length=120), nullable=False, server_default=""
            ),
            # Tablonun VAR OLMA SEBEBİ; NOT NULL (gerekçe başlıkta).
            sa.Column("interval_days", sa.Integer(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column(
                "status", sa.String(length=20), nullable=False, server_default="ACTIVE"
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "interval_days >= 0 AND interval_days <= %d" % EN_COK_GUN,
                name="ck_ppb_interval_range",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            # Çıplak değil BİLEŞİK: çapraz kiracı ürün referansını engeller.
            sa.ForeignKeyConstraint(
                ["company_id", "product_id"],
                [f"{URUN}.company_id", f"{URUN}.id"],
                name="fk_ppb_product_same_company",
            ),
            # Bu göçün TÜM SEBEBİ: tekillik ARDIL BİTKİYİ de kapsıyor.
            sa.UniqueConstraint(
                "company_id", "product_id", "crop", "next_crop",
                name="uq_ppb_company_product_crop_next",
            ),
            sa.UniqueConstraint("company_id", "id", name="uq_ppb_company_id"),
        )
        op.create_index("ix_ppb_company_product", PLANTBACK, ["company_id", "product_id"])

    # --- (b) faaliyette giriş yasağının kökeni ----------------------------
    inspector = sa.inspect(bind)
    mevcut = _sutunlar(inspector, FAALIYET)
    if "reentry_source" not in mevcut or "catalogue_reentry_days" not in mevcut:
        with op.batch_alter_table(FAALIYET) as batch:
            if "reentry_source" not in mevcut:
                # 0063'ün `preharvest_source`u ile AYNI genişlik ve AYNI
                # sözlük (CATALOGUE | OPERATOR | OPERATOR_OVERRIDE).
                batch.add_column(
                    sa.Column("reentry_source", sa.String(length=20), nullable=True)
                )
            if "catalogue_reentry_days" not in mevcut:
                batch.add_column(
                    sa.Column("catalogue_reentry_days", sa.Integer(), nullable=True)
                )

    # --- (c) sezonda plant-back uyarısı ve gerekçesi ----------------------
    inspector = sa.inspect(bind)
    sezon = _sutunlar(inspector, SEZON)
    if "plantback_warning" not in sezon:
        # SİSTEMİN bulduğu (0048 kuralı).
        op.add_column(SEZON, sa.Column("plantback_warning", sa.Text(), nullable=True))
    if "plantback_override_reason" not in sezon:
        # KULLANICININ söylediği (0048 kuralı).
        op.add_column(
            SEZON, sa.Column("plantback_override_reason", sa.Text(), nullable=True)
        )

    # --- (d) firma politikası ---------------------------------------------
    inspector = sa.inspect(bind)
    firma = _sutunlar(inspector, FIRMA)
    if "farm_plantback_policy" not in firma:
        sutun = sa.Column(
            "farm_plantback_policy",
            sa.String(length=20),
            nullable=False,
            server_default="require_reason",
        )
        # DİYALEKT AYRIMI ÖLÇÜLDÜ, VARSAYILMADI. İlk yazımda sütun ve CHECK
        # TEK bir `batch_alter_table` içindeydi ("0071'in dersi: SQLite'ta
        # ikisini ayrı çağırmak tabloyu iki kez yeniden kurar"). SQLite'ta
        # ÇALIŞTI; PostgreSQL'de CHECK SESSİZCE KURULMADI — `\d companies`
        # yalnız `ck_companies_service_parts_mode`i gösterdi ve
        # `UPDATE ... ='allow'` KABUL EDİLDİ. Yani batch'in
        # `create_check_constraint`i yeniden kurulum YAPILMAYAN diyalektte
        # hiçbir DDL üretmiyor (alembic 1.19.2 / SQLAlchemy 2.0.52).
        #
        # SESSİZ olduğu için tehlikeli: göç yeşil biter, kısıt yoktur.
        # Ayrım o yüzden AÇIK yazılıyor — SQLite tabloyu YENİDEN KURARAK
        # kısıtı `CREATE TABLE` metnine alıyor, ötekiler `ALTER TABLE ...
        # ADD CONSTRAINT` ile alıyor.
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(FIRMA) as batch:
                batch.add_column(sutun)
                batch.create_check_constraint(
                    "ck_companies_farm_plantback_policy", _seviye_check()
                )
        else:
            op.add_column(FIRMA, sutun)
            op.create_check_constraint(
                "ck_companies_farm_plantback_policy", FIRMA, _seviye_check()
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    firma = _sutunlar(inspector, FIRMA)
    if "farm_plantback_policy" in firma:
        # CHECK ÖNCE, sütun SONRA ve İKİSİ AYNI BATCH'te: batch yeniden
        # kurulumu yansıttığı CHECK'i yeni tabloya taşır ve o CHECK az önce
        # düşürülmüş bir sütunu adıyla anardı (0071'de ölçülen kusur).
        # DÜŞÜRME KOŞULLU ve gerekçesi ÖLÇÜLDÜ, VARSAYILMADI: SQLAlchemy
        # 2.0.52'de SQLite yansıtması bu CHECK'i GÖRMÜYOR (aynı tablodaki
        # `ck_companies_service_parts_mode`i görüyor) ve koşulsuz bir
        # `drop_constraint` `ValueError: No such constraint` ile PATLIYOR —
        # ölçüldü: CPython 3.12, alembic 1.19.2, SQLAlchemy 2.0.52,
        # `alembic downgrade -1`. Görülmeyen kısıt yeniden kuruluma da
        # TAŞINMIYOR, yani atlamak güvenlidir; PostgreSQL kısıtı GÖRÜR ve
        # orada açıkça düşüyor. Koşul iki dünyayı da kapsıyor: bir gün SQLite
        # yansıtması bu kısıtı görmeye başlarsa düşürme KENDİLİĞİNDEN
        # devreye girer ve 0071'in kusuru geri gelmez.
        kisitlar = {k.get("name") for k in inspector.get_check_constraints(FIRMA)}
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(FIRMA) as batch:
                if "ck_companies_farm_plantback_policy" in kisitlar:
                    batch.drop_constraint(
                        "ck_companies_farm_plantback_policy", type_="check"
                    )
                batch.drop_column("farm_plantback_policy")
        else:
            if "ck_companies_farm_plantback_policy" in kisitlar:
                op.drop_constraint(
                    "ck_companies_farm_plantback_policy", FIRMA, type_="check"
                )
            op.drop_column(FIRMA, "farm_plantback_policy")

    inspector = sa.inspect(bind)
    sezon = _sutunlar(inspector, SEZON)
    if "plantback_override_reason" in sezon:
        op.drop_column(SEZON, "plantback_override_reason")
    if "plantback_warning" in sezon:
        op.drop_column(SEZON, "plantback_warning")

    inspector = sa.inspect(bind)
    mevcut = _sutunlar(inspector, FAALIYET)
    if "reentry_source" in mevcut or "catalogue_reentry_days" in mevcut:
        with op.batch_alter_table(FAALIYET) as batch:
            if "catalogue_reentry_days" in mevcut:
                batch.drop_column("catalogue_reentry_days")
            if "reentry_source" in mevcut:
                batch.drop_column("reentry_source")

    inspector = sa.inspect(bind)
    if PLANTBACK in set(inspector.get_table_names()):
        op.drop_index("ix_ppb_company_product", table_name=PLANTBACK)
        op.drop_table(PLANTBACK)
