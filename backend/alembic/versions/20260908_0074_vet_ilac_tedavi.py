"""VETERİNER İLAÇ KATALOĞU + TEDAVİ KAYDI + ARINMA (BEKLEME) KİLİTLERİ (E2).

Konu: hayvancılık modülünde İLAÇ KALINTISI. Bu göç ŞEMAYI kurar; davranış
`app/routers/herd.py`nin tedavi, süt ve hareket yollarındadır.

--- ÖLÇÜLEN KUSUR: HAYVANCILIKTA ARINMA SÜRESİ KAVRAMI HİÇ YOKTU ----------

Tarla tarafında zincir TAM: BKÜ kataloğu (0063) PHI ve giriş yasağı günlerini
etiketten tutuyor, `_hasat_guvenlik_dogrula` hasat yazma yolunda ısırıyor,
0072 plant-back ile ardıl sezonu da kapattı.

Hayvancılık tarafında bu zincirin HİÇBİR HALKASI yoktu. Depoda ölçüldü
(`treatment`, `withdrawal`, `arinma`, `bekleme` literalleri `app/routers/
herd.py` ve `app/herd_*.py` içinde): SIFIR isabet. `animal_vaccinations` bir
AŞI defteridir ve aşının kalıntı süresi yoktur; ilaç tedavisinin tutulacağı
bir yer YOKTU.

Sonuç, tarla tarafındaki 0063 öncesi durumdan DAHA KÖTÜYDÜ: orada sayı elle
GİRİLEBİLİYORDU (ve çoğu zaman boş kalıyordu), burada GİRİLECEK ALAN YOKTU.
Yani antibiyotik uygulanmış bir ineğin sütü, sistem HİÇBİR ŞEY BİLMEDEN
tanka yazılabiliyordu.

--- DEPO HİÇBİR ARINMA SÜRESİ İDDİA ETMEZ --------------------------------

0063'ün duruşu BURADA DA GEÇERLİ ve gerekçesi AYNI: bu göç TEK BİR SATIR
VERİ YAZMAZ. Başlangıç ilaç listesi, tür başına varsayılan, "antibiyotikler
için 7 gün" gibi bir kod sabiti YOK.

Arınma süresi (süt için `milk_withdrawal_days`, et için
`meat_withdrawal_days`) YASAL bir süredir ve kaynağı VETERİNER İLACININ
PROSPEKTÜSÜDÜR. Depoya gömülen bir rakam, yanlış olduğunda deponun iddiası
olur ve o iddia bir gün insan sağlığına dokunur. Kataloğu firma doldurur;
depo yalnız saklar ve hatırlatır.

--- ŞEMA: ÜRÜN BAŞINA DEĞİL, ÜRÜN + TÜR BAŞINA ---------------------------

Aynı etken madde sığırda ve koyunda FARKLI arınma süresi taşır. 0063'ün
`(product_id, crop)` tercihi burada `(product_id, species)` olarak
tekrarlanıyor ve `species` BOŞ DİZE olabilir; boş dize "BÜTÜN TÜRLER"
demektir. Çözüm sırası türe ÖZEL satırı tercih eder, yoksa türden BAĞIMSIZ
satıra düşer.

NEDEN NULL DEĞİL BOŞ DİZE: 0063'ün gerekçesinin aynısı — SQL'de UNIQUE
NULL'ları BİRBİRİNDEN FARKLI sayar, yani `species` NULL olsaydı aynı ilaca
sınırsız sayıda "bütün türler" satırı girilebilir ve çözüm HANGİSİNİ
seçeceğini bilemezdi.

`crop`TAN BİR FARK VAR ve ŞEMAYA YAZILDI. `crop_seasons.crop` SERBEST
METİNDİR (0044), bu yüzden 0063 eşleştirmeyi Python'da Türkçe katlamayla
yapmak ZORUNDA kaldı. `animals.species` ise KAPALI BİR KÜMEDİR
(`ck_animals_species`, 0049: CATTLE/BUFFALO/SHEEP/GOAT). Bu yüzden burada
`ck_vet_drugs_species` var ve eşleştirme TAM EŞİTLİKTİR — katlamaya gerek
YOK; kısıt olmasaydı yanlış yazılmış bir tür kodu kataloğu SESSİZCE işlevsiz
bırakırdı (satır girilir, hiçbir hayvana eşleşmez).

--- `product_id` NULL KABUL ETMEZ (0063'ün kuralı) -----------------------

Katalog satırı MUTLAKA bir stok ürününü tarif eder. Ürüne bağlı OLMAYAN bir
satır hiçbir tedaviyi çözemez; doldurulup hiç kullanılmayan bir alan olurdu.

Tedavi KALEMİ ise (`animal_treatment_items.product_id`) NULL KABUL EDER ve
bu ASİMETRİ 0063'ünkiyle AYNI gerekçeye dayanır: serbest metin ilaç adı
(`drug_name`) çözülmez ve çözülmemelidir — veteriner kendi getirdiği, depoda
stok kartı olmayan bir ilacı da kaydedebilmeli. O kalem boş arınma süresiyle
kalır, boş da ihlal değildir.

--- İKİ SÜRE, İKİ AYRI KİLİT ---------------------------------------------

`milk_withdrawal_days` SÜT kilidini, `meat_withdrawal_days` ET kilidini
besler ve İKİSİ AYRI SÜTUNDUR çünkü prospektüste de ayrıdır: bir ilacın süt
arınması 3 gün, et arınması 28 gün olabilir. Tek sütuna indirmek, ikisinden
birini YALAN söyletirdi.

Katalogda İKİSİ DE NOT NULL ve tablonun VAR OLMA SEBEBİDİR.
`animal_treatments`ta ise ikisi de NULL KABUL EDER, çünkü orada tutulan şey
ÇÖZÜLMÜŞ ETKİN DEĞERDİR ve hiçbir şey çözülemediğinde boş kalır.

--- TEDAVİ: HAYVAN YA DA GRUP, İKİSİ BİRDEN DEĞİL -------------------------

`milk_yields`in 0049'da kurduğu desen: `animal_id` ve `group_id` ikisi de
NULLABLE, ikisi de BİLEŞİK yabancı anahtarlı, ve tam olarak BİRİ dolu.
Küçükbaşta bireysel kayıt tutulmaz — sürünün tamamı ilaçlanır ve tedavi
kaydı da sürüye yazılır. Yalnız `animal_id` olsaydı o firma sistemi HİÇ
KULLANAMAZDI.

Kısıt `ck_animal_treatments_hedef` ile ŞEMAYA yazıldı: `milk_yields`te bu
kural yalnız uygulama katmanındaydı (0049'un kendi notu bunu söylüyor) ve
uygulama katmanı bir betiği ya da elle yazılmış bir INSERT'ü durdurmaz.

--- KÖKEN KAYIT ALTINDA: 0048/0063'ün DESENİ AYNEN ------------------------

`withdrawal_source`      — etkin değeri KİM koydu
`catalogue_milk_days`    — katalog süt için NE demişti
`catalogue_meat_days`    — katalog et için NE demişti

Sözlük 0063/0072 ile BİREBİR AYNI: CATALOGUE | OPERATOR | OPERATOR_OVERRIDE.
İkinci bir sözlük uydurmak, denetçiye aynı olguyu iki dilde okuturdu.

`milk_yields` ve `animal_movements` üzerindeki `withdrawal_warning` ile
`withdrawal_override_reason` de 0048'in ayrımıdır: SİSTEMİN bulduğu ile
KULLANICININ söylediği AYRI sütunda durur. Aynı sütunda tutmak, denetimde
kimin ne dediğini ayırt edilemez kılardı.

--- FİRMA POLİTİKASI: ÜÇ SEVİYE, "allow" YOK ------------------------------

`herd_withdrawal_policy` — block | require_reason | warn. 0048/0064/0072 ile
AYNI sınır: kontrolü TAMAMEN kapatan bir seviye YOK, çünkü o seviye sessiz
bir güvenlik düğmesi olurdu ve burada kapatılan şey İNSAN GIDASIDIR.

SÜTUN VE CHECK AYRI AYRI SORULUYOR — 0072'DE ÖLÇÜLMÜŞ BİR KUSUR YÜZÜNDEN.
`app/tenancy.py` `companies`i `Table()` olarak bildiriyor ve uygulamanın
AÇILIŞI o tabloyu alembic'ten ÖNCE kurabiliyor; sütun bildirime de eklendiği
için tek bir `if POLITIKA_SUTUNU not in firma:` dalı CHECK'i HİÇ KURMAZDI ve
göç YEŞİL biterdi. 0072'de bu tam olarak ölçüldü (PostgreSQL 16, taze şema:
`UPDATE companies SET farm_plantback_policy='allow'` KABUL EDİLDİ).

--- DİYALEKT AYRIMI AÇIK YAZILDI: 0071/0072/0073'ün DERSLERİ --------------

0072'de ÖLÇÜLDÜ: `batch_alter_table` içindeki `create_check_constraint`
PostgreSQL'de SESSİZCE hiçbir DDL üretmiyor (alembic 1.19.2 /
SQLAlchemy 2.0.52) — batch yalnız tabloyu YENİDEN KURAN diyalektte (SQLite)
kısıtı `CREATE TABLE` metnine alıyor.

0071'in dersi tersidir: SQLite'ta var olan bir tabloyu İKİ AYRI batch ile
değiştirmek onu İKİ KEZ yeniden kurar ve arada yansıtılan kısıt henüz
olmayan/az önce düşen bir sütunu adıyla anarsa `OperationalError` verir.

İkisi birlikte: `companies` dalında SQLite -> sütun + CHECK TEK batch,
ötekiler -> `op.add_column` / `op.create_check_constraint` AÇIK.

`milk_yields` ve `animal_movements` dallarında CHECK YOK, yalnız iki NULL
TEXT sütunu var; orada tek batch YETER ve iki diyalekt de aynı sonuca varır.

--- GERİYE DOLDURMA YOK ---------------------------------------------------

Mevcut `milk_yields` / `animal_movements` satırları uyarı DEVRALMAZ; iki
sütun da NULL açılıyor ve NULL "bu satır arınma çağından önce yazıldı"
demektir. 0063'ün gerekçesi: bugün temiz kaydedilen bir sağım yarın da temiz
kaydedilmelidir. Geriye doldurma, kullanıcının yapmadığı bir değişiklik
yüzünden geçmiş kayıtları ihlale düşürürdü.

--- SAYISAL MANİFESTO: ÖLÇÜLDÜ, VARSAYILMADI -----------------------------

`animal_treatment_items.dose` `NUMERIC(14,4)`tür. Bu göç `core_schema.py`ye
DOKUNMUYOR ve `capture_numeric_snapshot` yalnız `core_schema` metadata'sını
gezer — yani yeni tablo ona GÖRÜNMEZ ve sayısal manifesto mutabakatında
varlık farkı ÜRETİLMEZ (0067/0073'ün gerekçesi, bu turda tabloların TAMAMI
yeni olduğu için daha da güçlü). Ölçek yine de yazılı: doz bir MİKTARDIR.

Revision ID: 20260908_0074
Revises: 20260908_0073
"""
from alembic import op
import sqlalchemy as sa

revision = "20260908_0074"
down_revision = "20260908_0073"
branch_labels = None
depends_on = None

KATALOG = "vet_drugs"
TEDAVI = "animal_treatments"
KALEM = "animal_treatment_items"
SUT = "milk_yields"
HAREKET = "animal_movements"
FIRMA = "companies"
URUN = "products"
HAYVAN = "animals"
GRUP = "animal_groups"

#: 0063'ün `ck_ppp_preharvest_range` sınırıyla AYNI. İki yer (şema, uç şeması)
#: farklı sınır söyleseydi bir değer forma girilip veritabanına yazılamaz
#: olurdu.
EN_COK_GUN = 3650

#: `animals.species` KAPALI KÜMESİ (0049 `ck_animals_species`). Boş dize
#: "bütün türler" demektir ve kümeye AYRICA ekleniyor.
TURLER = ("CATTLE", "BUFFALO", "SHEEP", "GOAT")

#: 0048/0064/0072'nin seviyeleri. "allow" YOK — gerekçe başlıkta.
POLITIKA_SEVIYELERI = ("block", "require_reason", "warn")

POLITIKA_SUTUNU = "herd_withdrawal_policy"
POLITIKA_CHECK = "ck_companies_herd_withdrawal_policy"

#: 0063/0072'nin köken sözlüğünün sütun genişliği; AYNI (20).
KOKEN_UZUNLUK = 20


def _sutunlar(inspector, tablo: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(tablo)}


def _liste_check(sutun: str, degerler: tuple[str, ...]) -> str:
    return "%s IN (%s)" % (sutun, ",".join("'%s'" % d for d in degerler))


def _politika_check() -> str:
    return _liste_check(POLITIKA_SUTUNU, POLITIKA_SEVIYELERI)


def _zaman_sutunlari() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    mevcut_tablolar = set(inspector.get_table_names())

    # --- (a) veteriner ilaç kataloğu ---------------------------------------
    if KATALOG not in mevcut_tablolar:
        op.create_table(
            KATALOG,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            # Bağsız satır hiçbir tedaviyi çözemez (0063'ün gerekçesi).
            sa.Column("product_id", sa.Integer(), nullable=False),
            # BOŞ DİZE = bütün türler. NULL DEĞİL — gerekçe başlıkta.
            sa.Column(
                "species", sa.String(length=40), nullable=False, server_default=""
            ),
            # Kataloğun VAR OLMA SEBEBİ; İKİSİ DE NOT NULL.
            sa.Column("milk_withdrawal_days", sa.Integer(), nullable=False),
            sa.Column("meat_withdrawal_days", sa.Integer(), nullable=False),
            # Uygulama yolu ve doz birimi PROSPEKTÜS bilgisidir; kilide girmez,
            # kayda girer. Zorunlu değil: firma elindeki bilgiyle başlayabilmeli.
            sa.Column("route", sa.String(length=40), nullable=True),
            sa.Column("dose_unit", sa.String(length=32), nullable=True),
            # Ruhsat numarası prospektüsün kimliğidir; denetimde "bu süre
            # nereden geldi" sorusunun cevabı (0063'ün `registration_no`su).
            sa.Column("registration_no", sa.String(length=60), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column(
                "status", sa.String(length=20), nullable=False, server_default="ACTIVE"
            ),
            # 0065'in köken çifti: satır elle mi girildi, içe mi aktarıldı.
            sa.Column(
                "origin", sa.String(length=20), nullable=False, server_default="MANUAL"
            ),
            sa.Column("origin_reference", sa.String(length=120), nullable=True),
            *_zaman_sutunlari(),
            sa.CheckConstraint(
                "milk_withdrawal_days >= 0 AND milk_withdrawal_days <= %d" % EN_COK_GUN,
                name="ck_vet_drugs_milk_range",
            ),
            sa.CheckConstraint(
                "meat_withdrawal_days >= 0 AND meat_withdrawal_days <= %d" % EN_COK_GUN,
                name="ck_vet_drugs_meat_range",
            ),
            # `crop`TAN FARK: tür KAPALI kümedir, o yüzden ŞEMADA kısıtlı ve
            # eşleştirme TAM EŞİTLİKTİR (gerekçe başlıkta).
            sa.CheckConstraint(
                _liste_check("species", ("",) + TURLER),
                name="ck_vet_drugs_species",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            # Çıplak değil BİLEŞİK: çapraz kiracı ürün referansını engeller.
            sa.ForeignKeyConstraint(
                ["company_id", "product_id"],
                [f"{URUN}.company_id", f"{URUN}.id"],
                name="fk_vet_drugs_product_same_company",
            ),
            # Aynı ürün+tür için İKİ satır olamaz; çözüm belirsiz kalmasın.
            sa.UniqueConstraint(
                "company_id", "product_id", "species",
                name="uq_vet_drugs_company_product_species",
            ),
            # İleride bileşik yabancı anahtar hedefi olabilmesi için.
            sa.UniqueConstraint("company_id", "id", name="uq_vet_drugs_company_id"),
        )
        op.create_index(
            "ix_vet_drugs_company_product", KATALOG, ["company_id", "product_id"]
        )

    # --- (b) tedavi defteri ------------------------------------------------
    if TEDAVI not in mevcut_tablolar:
        op.create_table(
            TEDAVI,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            # HAYVAN YA DA GRUP — ikisi de NULLABLE, tam olarak BİRİ dolu
            # (aşağıdaki CHECK). `milk_yields`in 0049 deseni.
            sa.Column("animal_id", sa.Integer(), nullable=True),
            sa.Column("group_id", sa.Integer(), nullable=True),
            sa.Column("treated_on", sa.Date(), nullable=False),
            sa.Column("veterinarian", sa.String(length=160), nullable=True),
            sa.Column("diagnosis", sa.String(length=200), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            # ÇÖZÜLMÜŞ ETKİN DEĞERLER. NULL KABUL EDER: ne katalog ne operatör
            # konuştuysa süre BOŞ kalır ve boş ihlal DEĞİLDİR (0063 kuralı).
            sa.Column("milk_withdrawal_days", sa.Integer(), nullable=True),
            sa.Column("meat_withdrawal_days", sa.Integer(), nullable=True),
            # 0063/0072 ile BİREBİR AYNI sözlük ve AYNI genişlik.
            sa.Column(
                "withdrawal_source", sa.String(length=KOKEN_UZUNLUK), nullable=True
            ),
            sa.Column("catalogue_milk_days", sa.Integer(), nullable=True),
            sa.Column("catalogue_meat_days", sa.Integer(), nullable=True),
            *_zaman_sutunlari(),
            sa.CheckConstraint(
                "(animal_id IS NULL) <> (group_id IS NULL)",
                name="ck_animal_treatments_hedef",
            ),
            sa.CheckConstraint(
                "milk_withdrawal_days IS NULL OR (milk_withdrawal_days >= 0 "
                "AND milk_withdrawal_days <= %d)" % EN_COK_GUN,
                name="ck_animal_treatments_milk_range",
            ),
            sa.CheckConstraint(
                "meat_withdrawal_days IS NULL OR (meat_withdrawal_days >= 0 "
                "AND meat_withdrawal_days <= %d)" % EN_COK_GUN,
                name="ck_animal_treatments_meat_range",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(
                ["company_id", "animal_id"],
                [f"{HAYVAN}.company_id", f"{HAYVAN}.id"],
                name="fk_animal_treatments_animal_same_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "group_id"],
                [f"{GRUP}.company_id", f"{GRUP}.id"],
                name="fk_animal_treatments_group_same_company",
            ),
            sa.UniqueConstraint(
                "company_id", "id", name="uq_animal_treatments_company_id"
            ),
        )
        # Kilit sorgusu HER sağımda ve HER satış/kesim hareketinde koşuyor;
        # tarama yolu hayvan ya da gruptur.
        op.create_index(
            "ix_animal_treatments_company_animal", TEDAVI, ["company_id", "animal_id"]
        )
        op.create_index(
            "ix_animal_treatments_company_group", TEDAVI, ["company_id", "group_id"]
        )

    # --- (c) tedavi kalemleri ----------------------------------------------
    if KALEM not in mevcut_tablolar:
        op.create_table(
            KALEM,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("treatment_id", sa.Integer(), nullable=False),
            # NULL KABUL EDER — asimetrinin gerekçesi başlıkta: serbest metin
            # ilaç çözülmez ve çözülmemeli.
            sa.Column("product_id", sa.Integer(), nullable=True),
            sa.Column("drug_name", sa.String(length=200), nullable=True),
            sa.Column("dose", sa.Numeric(14, 4), nullable=True),
            sa.Column("dose_unit", sa.String(length=32), nullable=True),
            *_zaman_sutunlari(),
            sa.CheckConstraint("dose IS NULL OR dose >= 0", name="ck_ati_dose_negatif_olamaz"),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(
                ["company_id", "treatment_id"],
                [f"{TEDAVI}.company_id", f"{TEDAVI}.id"],
                name="fk_ati_treatment_same_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "product_id"],
                [f"{URUN}.company_id", f"{URUN}.id"],
                name="fk_ati_product_same_company",
            ),
            sa.UniqueConstraint(
                "company_id", "id", name="uq_animal_treatment_items_company_id"
            ),
        )
        op.create_index(
            "ix_ati_company_treatment", KALEM, ["company_id", "treatment_id"]
        )

    # --- (d) süt kaydında uyarı ve gerekçe (0048 ayrımı) -------------------
    inspector = sa.inspect(bind)
    sut = _sutunlar(inspector, SUT)
    if "withdrawal_warning" not in sut or "withdrawal_override_reason" not in sut:
        # TEK BATCH: 0071'in dersi (iki ayrı batch tabloyu iki kez yeniden
        # kurar). CHECK YOK, o yüzden 0072'nin diyalekt ayrımı burada
        # GEREKMİYOR — `add_column` iki diyalektte de gerçek DDL üretir.
        with op.batch_alter_table(SUT) as batch:
            if "withdrawal_warning" not in sut:
                # SİSTEMİN bulduğu.
                batch.add_column(
                    sa.Column("withdrawal_warning", sa.Text(), nullable=True)
                )
            if "withdrawal_override_reason" not in sut:
                # KULLANICININ söylediği.
                batch.add_column(
                    sa.Column("withdrawal_override_reason", sa.Text(), nullable=True)
                )

    # --- (e) hareket kaydında uyarı ve gerekçe -----------------------------
    inspector = sa.inspect(bind)
    hareket = _sutunlar(inspector, HAREKET)
    if (
        "withdrawal_warning" not in hareket
        or "withdrawal_override_reason" not in hareket
    ):
        with op.batch_alter_table(HAREKET) as batch:
            if "withdrawal_warning" not in hareket:
                batch.add_column(
                    sa.Column("withdrawal_warning", sa.Text(), nullable=True)
                )
            if "withdrawal_override_reason" not in hareket:
                batch.add_column(
                    sa.Column("withdrawal_override_reason", sa.Text(), nullable=True)
                )

    # --- (f) firma politikası ----------------------------------------------
    inspector = sa.inspect(bind)
    firma = _sutunlar(inspector, FIRMA)
    # SÜTUN VE CHECK AYRI AYRI SORULUYOR — 0072'de ÖLÇÜLMÜŞ kusur, gerekçe
    # başlıkta. `companies` AÇILIŞ DDL'inde bildiriliyor ve tek koşullu bir dal
    # CHECK'i SESSİZCE atlardı.
    sutun_eksik = POLITIKA_SUTUNU not in firma
    kisitlar = {k.get("name") for k in inspector.get_check_constraints(FIRMA)}
    # SQLite bu CHECK'i YANSITMIYOR (0072'de ölçüldü), yani orada `check_eksik`
    # HER ZAMAN doğrudur. Zararsız: `upgrade` bir veritabanında BİR KEZ koşar
    # ve yansıtılmayan kısıt yeniden kuruluma da taşınmaz.
    check_eksik = POLITIKA_CHECK not in kisitlar
    if sutun_eksik or check_eksik:
        sutun = sa.Column(
            POLITIKA_SUTUNU,
            sa.String(length=20),
            nullable=False,
            server_default="require_reason",
        )
        # DİYALEKT AYRIMI AÇIK: batch'in `create_check_constraint`i yeniden
        # kurulum YAPILMAYAN diyalektte hiçbir DDL üretmiyor (0072'de ölçüldü,
        # alembic 1.19.2 / SQLAlchemy 2.0.52).
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(FIRMA) as batch:
                if sutun_eksik:
                    batch.add_column(sutun)
                if check_eksik:
                    batch.create_check_constraint(POLITIKA_CHECK, _politika_check())
        else:
            if sutun_eksik:
                op.add_column(FIRMA, sutun)
            if check_eksik:
                op.create_check_constraint(POLITIKA_CHECK, FIRMA, _politika_check())


def downgrade() -> None:
    """Simetrik ve KOŞULLU. Tabloları düşürmek VERİ KAYBIDIR ve bilinçlidir.

    `vet_drugs` / `animal_treatments` / `animal_treatment_items` bu göçle
    DOĞDU; onları düşürmek göçten sonra girilmiş tedavi kaydını siler. Geri
    alma zaten bunu ister — ama 0073'ün "tablo BOŞ olmalı" nöbetçisi BURADA
    YOK ve farkın gerekçesi şudur: 0073 VAR OLAN bir tablonun ŞEKLİNİ
    değiştiriyordu ve mevcut satırlar için anlamlı bir varsayılan
    ÜRETEMİYORDU; burada tablonun TAMAMI bu göçün eseridir, yani geri alma
    "bu göçün getirdiğini götür" demektir ve başka bir yorumu yoktur.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    firma = _sutunlar(inspector, FIRMA)
    if POLITIKA_SUTUNU in firma:
        # CHECK ÖNCE, sütun SONRA ve İKİSİ AYNI BATCH'te: batch yeniden
        # kurulumu yansıttığı CHECK'i yeni tabloya taşır ve o CHECK az önce
        # düşürülmüş bir sütunu adıyla anardı (0071'de ölçülen kusur).
        # DÜŞÜRME KOŞULLU: SQLite yansıtması bu CHECK'i GÖRMÜYOR (0072'de
        # ölçüldü) ve koşulsuz bir `drop_constraint` `ValueError` ile PATLAR.
        kisitlar = {k.get("name") for k in inspector.get_check_constraints(FIRMA)}
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(FIRMA) as batch:
                if POLITIKA_CHECK in kisitlar:
                    batch.drop_constraint(POLITIKA_CHECK, type_="check")
                batch.drop_column(POLITIKA_SUTUNU)
        else:
            if POLITIKA_CHECK in kisitlar:
                op.drop_constraint(POLITIKA_CHECK, FIRMA, type_="check")
            op.drop_column(FIRMA, POLITIKA_SUTUNU)

    for tablo in (HAREKET, SUT):
        inspector = sa.inspect(bind)
        mevcut = _sutunlar(inspector, tablo)
        if "withdrawal_warning" in mevcut or "withdrawal_override_reason" in mevcut:
            with op.batch_alter_table(tablo) as batch:
                if "withdrawal_override_reason" in mevcut:
                    batch.drop_column("withdrawal_override_reason")
                if "withdrawal_warning" in mevcut:
                    batch.drop_column("withdrawal_warning")

    inspector = sa.inspect(bind)
    tablolar = set(inspector.get_table_names())
    # KALEM ÖNCE: tedaviye bileşik yabancı anahtarla bağlı.
    if KALEM in tablolar:
        op.drop_index("ix_ati_company_treatment", table_name=KALEM)
        op.drop_table(KALEM)
    if TEDAVI in tablolar:
        op.drop_index("ix_animal_treatments_company_group", table_name=TEDAVI)
        op.drop_index("ix_animal_treatments_company_animal", table_name=TEDAVI)
        op.drop_table(TEDAVI)
    if KATALOG in tablolar:
        op.drop_index("ix_vet_drugs_company_product", table_name=KATALOG)
        op.drop_table(KATALOG)
