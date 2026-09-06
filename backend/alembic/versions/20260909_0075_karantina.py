"""HAYVAN/SÜRÜ KARANTİNASI ve KARANTİNA KİLİTLERİ (E3).

Konu: hayvancılık modülünde KARANTİNA. Bu göç ŞEMAYI kurar; davranış
`app/routers/herd.py`nin karantina, süt ve hareket yollarındadır.

--- ÖLÇÜLEN KUSUR: KARANTİNA KAVRAMI HİÇ YOKTU ---------------------------

Depoda ölçüldü (`quarantine`, `karantina`, `izolasyon` literalleri
`app/routers/herd.py`, `app/herd_*.py` ve `alembic/versions/` içinde): SIFIR
isabet. E2 (göç 0074) İLAÇ KALINTISINI kapattı; karantina BAŞKA BİR OLGUDUR
ve E2'nin hiçbir halkası onu tutmuyordu:

  * Arınma süresi bir İLACIN prospektüsünden gelir ve GÜN SAYISIDIR; bitişi
    tedavi gününden HESAPLANIR.
  * Karantina bir KARARDIR — hayvan hasta, şüpheli, yeni gelmiş ya da resmî
    tedbir altındadır. Ne zaman biteceği YAZILDIĞI AN BİLİNMEZ; biten şeyi
    bitiren insandır, aritmetik değil.

Bu ayrım şemaya YAZILDI: `ended_on` NULL KABUL EDER ve NULL "HALA AÇIK"
demektir. Arınma deseni (başlangıç + gün sayısı) burada kullanılsaydı, süresi
belirsiz bir karantina için uydurma bir gün sayısı girmek ZORUNLU olurdu ve o
sayı deponun İDDİASI olurdu.

--- `animals.status` KARANTİNA İÇİN KULLANILMADI (ÖLÇÜLDÜ) ---------------

`animals.status` kapalı kümesi ACTIVE | SOLD | DEAD | SLAUGHTERED | ARCHIVED
(0049) ve o küme bir YAŞAM DÖNGÜSÜDÜR: her değer hayvanın işletmedeki
varlığıyla ilgilidir ve `MOVEMENT_TO_STATUS` hareketlerden TÜRETİR.
Karantina oraya bir değer olarak eklenseydi:

  * "kaç hayvanım var" sorusu karantinadaki hayvanı SAYMAZ olurdu — oysa
    hayvan işletmededir ve sağılmaya devam eder;
  * karantina biterken hayvanın ÖNCEKİ durumu (ACTIVE mi ARCHIVED mi) hiçbir
    yerde tutulmadığı için GERİ ALINAMAZDI;
  * iki karantina arasındaki tarih aralığı KAYBOLURDU — durum sütunu bir AN'ı
    tutar, bir ARALIĞI değil.

Karantina bu yüzden KENDİ TABLOSUNDADIR ve `animals.status` KIMILDAMADI.

--- HAYVAN YA DA GRUP, İKİSİ BİRDEN DEĞİL --------------------------------

`milk_yields`in 0049'da, `animal_treatments`ın 0074'te kurduğu desen aynen:
`animal_id` ve `group_id` ikisi de NULLABLE, ikisi de BİLEŞİK yabancı
anahtarlı, ve tam olarak BİRİ dolu (`ck_animal_quarantines_hedef`).

Küçükbaşta bireysel kayıt tutulmaz ve karantina zaten çoğu zaman SÜRÜYE
konur: yeni gelen bir sürü topluca gözlem altına alınır. Yalnız `animal_id`
olsaydı o işletme kuralı HİÇ KULLANAMAZDI.

`animal_groups`ta TÜR AYRIMI YOK (ÖLÇÜLDÜ: tabloda `kind` sütunu YOKTUR), yani
"karantina sürüsü" diye ayrı bir grup tipi UYDURULMADI; karantina grubun
KENDİSİNE değil, gruba yazılan bir KAYDA bağlıdır.

--- AÇIK KARANTİNA HEDEF BAŞINA TEKTİR -----------------------------------

Aynı hayvana ikinci bir AÇIK karantina, "bu hayvan ne zaman çıkacak"
sorusunu CEVAPSIZ bırakırdı: iki satır iki farklı `reason` ve iki farklı
kapanış taşır ve hangisinin kapanışının hayvanı serbest bıraktığı belirsiz
kalır. Kapanmış karantinalar ise SINIRSIZ sayıda olabilir — geçmiş bir
defterdir.

KISMİ TEKİL İNDEKS ile yazıldı ve İKİ AYRI indeks olmasının gerekçesi
ÖLÇÜLMÜŞTÜR: SQL'de UNIQUE NULL'ları BİRBİRİNDEN FARKLI sayar, yani
`(company_id, animal_id, group_id)` üzerinde TEK bir indeks, sürü
karantinalarında `animal_id` NULL olduğu için HİÇBİR ŞEYİ engellemezdi.
İki indeks, her biri kendi sütununun NULL OLMADIĞI satırları süzüyor.

İKİ DİYALEKT DE KISMİ İNDEKSİ DESTEKLER (ölçüldü: SQLite 3.8.0+ ve
PostgreSQL). Yine de `sqlite_where` ve `postgresql_where` AYRI AYRI veriliyor
çünkü alembic/SQLAlchemy koşulu diyalekt adıyla etiketlenmiş argümandan okur;
tek bir `where` argümanı YOKTUR ve biri yazılmasaydı O diyalektte indeks
KOŞULSUZ (yani her satırı kapsayan) kurulurdu — kapanmış karantinaları da
tekilleştirir ve ikinci bir karantinayı SESSİZCE reddederdi.

--- FİRMA POLİTİKASI: ÜÇ SEVİYE, "allow" YOK, VARSAYILAN `block` ---------

`herd_quarantine_policy` — block | require_reason | warn. 0048/0064/0072/0074
ile AYNI sınır: kontrolü TAMAMEN kapatan bir seviye YOK.

VARSAYILAN `block`TUR ve KARDEŞLERİNDEN (`require_reason`) FARKLI OLMASI
BİLİNÇLİDİR. Arınma süresi HESAPLANMIŞ bir kısıttır: katalog yanlış
doldurulmuş olabilir, süre hayvana uymuyor olabilir, o yüzden gerekçeli geçiş
makul bir varsayılandır. Karantina ise HESAPLANMAZ — bir insan onu ELLE
açmıştır ve hâlâ AÇIK bırakmıştır. Kararı verenin kendisi kapatmadan, o
kararın etrafından bir gerekçe metniyle dolaşmak varsayılan davranış OLAMAZ:
doğru yol karantinayı KAPATMAKTIR ve kapatma bu göçün açtığı bir uçtur.
Gevşetmek isteyen firma ayarı `require_reason`a çekebilir; kapatamaz.

SÜTUN VE CHECK AYRI AYRI SORULUYOR — 0072'de ölçülüp 0074'te tekrarlanan
kusur yüzünden. `app/tenancy.py` `companies`i `Table()` olarak bildiriyor ve
uygulamanın AÇILIŞI o tabloyu alembic'ten ÖNCE kurabiliyor; sütun bildirime de
eklendiği için tek bir `if POLITIKA_SUTUNU not in firma:` dalı CHECK'i HİÇ
KURMAZDI ve göç YEŞİL biterdi.

--- UYARI/GEREKÇE ÇİFTİ AYRIDIR: 0074'ÜN ÇİFTİNE BİNDİRİLMEDİ ------------

`milk_yields` ve `animal_movements` üzerine `quarantine_warning` ve
`quarantine_override_reason` AYRI SÜTUNLAR olarak ekleniyor; 0074'ün
`withdrawal_*` çifti YERİNDE KALIYOR ve DOKUNULMUYOR.

Gerekçe ÖLÇÜLEBİLİR: bir sağım HEM arınma HEM karantina ihlal edebilir. Tek
çifte bindirmek, ikinci uyarının birinciyi EZMESİ ya da iki farklı olgunun tek
metinde birleşip denetimde AYRIŞTIRILAMAZ olması demekti — ve gerekçe de
öyle: kullanıcının "veteriner onayı, süt buzağıya" demesi arınma için geçerli
bir gerekçedir, karantina için DEĞİLDİR.

--- GERİYE DOLDURMA YOK ---------------------------------------------------

Mevcut `milk_yields` / `animal_movements` satırları uyarı DEVRALMAZ; iki sütun
da NULL açılıyor ve NULL "bu satır karantina çağından önce yazıldı" demektir.
0063/0074'ün gerekçesi: bugün temiz kaydedilen bir sağım yarın da temiz
kaydedilmelidir.

--- SAYISAL MANİFESTO -----------------------------------------------------

Bu göç TEK BİR SAYISAL (NUMERIC) SÜTUN AÇMIYOR: karantina bir TARİH
ARALIĞIDIR, bir miktar değil. `core_schema.py`ye de DOKUNULMUYOR, yani
`capture_numeric_snapshot` için varlık farkı ÜRETİLMİYOR.

Revision ID: 20260909_0075
Revises: 20260908_0074
"""
from alembic import op
import sqlalchemy as sa

revision = "20260909_0075"
down_revision = "20260908_0074"
branch_labels = None
depends_on = None

KARANTINA = "animal_quarantines"
SUT = "milk_yields"
HAREKET = "animal_movements"
FIRMA = "companies"
HAYVAN = "animals"
GRUP = "animal_groups"

#: 0048/0064/0072/0074'ün seviyeleri. "allow" YOK — gerekçe başlıkta.
POLITIKA_SEVIYELERI = ("block", "require_reason", "warn")

POLITIKA_SUTUNU = "herd_quarantine_policy"
POLITIKA_CHECK = "ck_companies_herd_quarantine_policy"

#: VARSAYILAN kardeşlerinden FARKLI — gerekçe başlıkta.
POLITIKA_VARSAYILAN = "block"

#: 0074'ün çiftinden AYRI iki sütun; gerekçe başlıkta.
UYARI_SUTUNU = "quarantine_warning"
GEREKCE_SUTUNU = "quarantine_override_reason"

#: Açık karantinanın kısmi tekil indeksleri. İKİ TANE — gerekçe başlıkta.
ACIK_HAYVAN_INDEKS = "uq_animal_quarantines_acik_hayvan"
ACIK_GRUP_INDEKS = "uq_animal_quarantines_acik_grup"
TARAMA_INDEKS = "ix_animal_quarantines_company_animal"
TARAMA_GRUP_INDEKS = "ix_animal_quarantines_company_group"

#: Kısmi indekslerin koşulları. İKİ DİYALEKTE DE AYNI METİN veriliyor; ayrı
#: argüman olmalarının gerekçesi başlıkta.
ACIK_HAYVAN_KOSUL = "ended_on IS NULL AND animal_id IS NOT NULL"
ACIK_GRUP_KOSUL = "ended_on IS NULL AND group_id IS NOT NULL"

#: "BOŞ SEBEP" kümesi ve neden TEK ARGÜMANLI `trim` YETMEDİĞİ — ÖLÇÜLDÜ,
#: VARSAYILMADI. `trim(x)` İKİ DİYALEKTTE DE YALNIZ BOŞLUK karakterini
#: kırpar; `length(trim(reason)) > 0` yazılmış ilk hâl, PostgreSQL 16'da TEK
#: SEKME (`\t`) taşıyan bir `reason`u KABUL ETTİ (ikizde kırmızı çıktı).
#:
#: Karakterler `chr()` ile kuruluyor ki KAYNAK OKUNABİLİR kalsın; SQL
#: literaline giren şey karakterlerin KENDİSİDİR. İki argümanlı `trim(X, Y)`
#: biçimi İKİ DİYALEKTTE DE aynı sonucu veriyor (ölçüldü: SQLite 3.x ve
#: PostgreSQL 16 üzerinde '', ' ', TAB, LF için 0/false; 'x' ve ' x ' için
#: 1/true).
BOSLUK_KARAKTERLERI = " " + chr(9) + chr(10) + chr(13)


def _sutunlar(inspector, tablo: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(tablo)}


def _politika_check() -> str:
    degerler = ",".join("'%s'" % d for d in POLITIKA_SEVIYELERI)
    return "%s IN (%s)" % (POLITIKA_SUTUNU, degerler)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- (a) karantina defteri --------------------------------------------
    if KARANTINA not in set(inspector.get_table_names()):
        op.create_table(
            KARANTINA,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            # HAYVAN YA DA GRUP — ikisi de NULLABLE, tam olarak BİRİ dolu.
            sa.Column("animal_id", sa.Integer(), nullable=True),
            sa.Column("group_id", sa.Integer(), nullable=True),
            sa.Column("started_on", sa.Date(), nullable=False),
            # NULL = HALA AÇIK. Gerekçe başlıkta: bitişi bilen insandır.
            sa.Column("ended_on", sa.Date(), nullable=True),
            # SEBEP ZORUNLU: sebepsiz bir karantina, kapatma kararını alacak
            # olana hiçbir şey söylemez ve "bu neden açıktı" sorusu denetimde
            # cevapsız kalırdı.
            sa.Column("reason", sa.String(length=120), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "(animal_id IS NULL) <> (group_id IS NULL)",
                name="ck_animal_quarantines_hedef",
            ),
            # ARALIK GERİYE AKMAZ. Eşitlik SERBEST: aynı gün açılıp kapanan
            # karantina (yanlış açıldı, aynı gün kapatıldı) geçerli bir
            # kayıttır ve onu reddetmek düzeltmeyi imkânsız kılardı.
            sa.CheckConstraint(
                "ended_on IS NULL OR ended_on >= started_on",
                name="ck_animal_quarantines_aralik",
            ),
            # BOŞLUKTAN İBARET SEBEP, sebepsizlikle AYNI ŞEYDİR. NOT NULL tek
            # başına ' ' dizesini geçirirdi. KIRPILAN KARAKTER KÜMESİ AÇIK
            # VERİLİYOR — gerekçesi `BOSLUK_KARAKTERLERI`nde ve ÖLÇÜLDÜ: tek
            # argümanlı `trim` SEKMEYİ kırpmıyor ve o hâl PostgreSQL ikizinde
            # kırmızı çıktı.
            sa.CheckConstraint(
                "length(trim(reason, '%s')) > 0" % BOSLUK_KARAKTERLERI,
                name="ck_animal_quarantines_sebep_dolu",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            # Çıplak değil BİLEŞİK: çapraz kiracı referansını engeller
            # (0062'nin kuralı, 0074'ün beş anahtarıyla aynı gerekçe).
            sa.ForeignKeyConstraint(
                ["company_id", "animal_id"],
                [HAYVAN + ".company_id", HAYVAN + ".id"],
                name="fk_animal_quarantines_animal_same_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "group_id"],
                [GRUP + ".company_id", GRUP + ".id"],
                name="fk_animal_quarantines_group_same_company",
            ),
            # İleride bileşik yabancı anahtar hedefi olabilmesi için.
            sa.UniqueConstraint(
                "company_id", "id", name="uq_animal_quarantines_company_id"
            ),
        )
        # Kilit sorgusu HER sağımda ve HER satış/kesim/nakil hareketinde
        # koşuyor; tarama yolu hayvan ya da gruptur.
        op.create_index(TARAMA_INDEKS, KARANTINA, ["company_id", "animal_id"])
        op.create_index(TARAMA_GRUP_INDEKS, KARANTINA, ["company_id", "group_id"])
        # KISMİ TEKİL: yalnız AÇIK satırlar. İki diyalektin koşulu AYRI AYRI
        # veriliyor — gerekçe başlıkta (tek `where` argümanı YOKTUR ve eksik
        # bırakılan diyalektte indeks KOŞULSUZ kurulurdu).
        op.create_index(
            ACIK_HAYVAN_INDEKS, KARANTINA, ["company_id", "animal_id"],
            unique=True,
            sqlite_where=sa.text(ACIK_HAYVAN_KOSUL),
            postgresql_where=sa.text(ACIK_HAYVAN_KOSUL),
        )
        op.create_index(
            ACIK_GRUP_INDEKS, KARANTINA, ["company_id", "group_id"],
            unique=True,
            sqlite_where=sa.text(ACIK_GRUP_KOSUL),
            postgresql_where=sa.text(ACIK_GRUP_KOSUL),
        )

    # --- (b) süt ve hareket kaydında uyarı ve gerekçe ----------------------
    # TEK BATCH: 0071'in dersi (iki ayrı batch tabloyu iki kez yeniden kurar).
    # CHECK YOK, o yüzden 0072/0074'ün diyalekt ayrımı burada GEREKMİYOR —
    # `add_column` iki diyalektte de gerçek DDL üretir.
    for tablo in (SUT, HAREKET):
        inspector = sa.inspect(bind)
        mevcut = _sutunlar(inspector, tablo)
        if UYARI_SUTUNU not in mevcut or GEREKCE_SUTUNU not in mevcut:
            with op.batch_alter_table(tablo) as batch:
                if UYARI_SUTUNU not in mevcut:
                    # SİSTEMİN bulduğu (0048'in ayrımı).
                    batch.add_column(sa.Column(UYARI_SUTUNU, sa.Text(), nullable=True))
                if GEREKCE_SUTUNU not in mevcut:
                    # KULLANICININ söylediği.
                    batch.add_column(
                        sa.Column(GEREKCE_SUTUNU, sa.Text(), nullable=True)
                    )

    # --- (c) firma politikası ----------------------------------------------
    inspector = sa.inspect(bind)
    firma = _sutunlar(inspector, FIRMA)
    # SÜTUN VE CHECK AYRI AYRI SORULUYOR — 0072'de ölçülüp 0074'te tekrarlanan
    # kusur, gerekçe başlıkta.
    sutun_eksik = POLITIKA_SUTUNU not in firma
    kisitlar = {k.get("name") for k in inspector.get_check_constraints(FIRMA)}
    # SQLite bu CHECK'i YANSITMIYOR (0072'de ölçüldü), yani orada `check_eksik`
    # HER ZAMAN doğrudur. Zararsız: `upgrade` bir veritabanında BİR KEZ koşar.
    check_eksik = POLITIKA_CHECK not in kisitlar
    if sutun_eksik or check_eksik:
        sutun = sa.Column(
            POLITIKA_SUTUNU,
            sa.String(length=20),
            nullable=False,
            server_default=POLITIKA_VARSAYILAN,
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
    """Simetrik ve KOŞULLU. Tabloyu düşürmek VERİ KAYBIDIR ve bilinçlidir.

    `animal_quarantines` bu göçle DOĞDU; düşürmek göçten sonra açılmış
    karantina kaydını siler. 0074'ün gerekçesi aynen geçerli: tablonun TAMAMI
    bu göçün eseridir, yani geri alma "bu göçün getirdiğini götür" demektir ve
    başka bir yorumu yoktur.
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
        if UYARI_SUTUNU in mevcut or GEREKCE_SUTUNU in mevcut:
            with op.batch_alter_table(tablo) as batch:
                if GEREKCE_SUTUNU in mevcut:
                    batch.drop_column(GEREKCE_SUTUNU)
                if UYARI_SUTUNU in mevcut:
                    batch.drop_column(UYARI_SUTUNU)

    inspector = sa.inspect(bind)
    if KARANTINA in set(inspector.get_table_names()):
        for indeks in (
            ACIK_GRUP_INDEKS, ACIK_HAYVAN_INDEKS,
            TARAMA_GRUP_INDEKS, TARAMA_INDEKS,
        ):
            op.drop_index(indeks, table_name=KARANTINA)
        op.drop_table(KARANTINA)
