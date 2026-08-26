"""Hayvancılık V1 — FAZ 1: veri modeli.

Revision ID: 20260808_0049
Revises: 20260807_0048

Konu: mobil-erp#17. Tarla V1'in yanına hayvan varlığı defteri.

ÜRÜN SINIRI TARLA İLE AYNI: stok ve muhasebeye doğrudan/örtük fiş YAZILMAZ.
Yem ve satış tutarı elle giriliyor; ileriki entegrasyon için
``herd_integration_events`` hazır duruyor ama V1'de yazan bir üretici YOK.

--- ARAŞTIRMAYA DAYANAN ÜÇ KARAR (kaynaklar konu #17'de) ------------------

1. KİMLİK KÜPE NUMARASI, BİZİM ÜRETTİĞİMİZ KOD DEĞİL. Sığır, koyun ve keçi
   küpelenip TÜRKVET'e kaydediliyor; küpe hayvanın YASAL kimliği. Tarla
   modülünde parsele içeriden `code` üretmiştik; burada aynısını yapmak
   kullanıcıyı aynı hayvan için iki numara taşımaya zorlardı.

2. KÜPE BİÇİMİ ŞU AN GEÇİŞTE — bu yüzden VERİTABANI BİÇİM DAYATMIYOR.
   28.06.2026 yönetmelik değişikliğiyle karekod ve turkuaz küpe geldi; il
   kodlu mevcut küpeler yeni doğanlarda 31.12.2026'ya kadar geçerli. Kontrol
   basamağı algoritması da açık kaynaklarda bulunamadı. Sütuna katı bir CHECK
   koymak, aylar içinde GEÇERLİ bir küpeyi reddetmek demekti — ve yanlış bir
   kısıt, hiç kısıt olmamasından kötüdür (migration ile geri almak gerekir).
   Biçim kontrolü uygulama katmanında ve UYARI olarak yapılacak.

3. AYNI KÜPE İKİ AKTİF HAYVANDA OLAMAZ — ama geçmişte olabilir. Küpe numarası
   yeniden kullanılabiliyor (hayvan ölür/satılır, numara döner). Bu yüzden
   tekillik KOŞULLU: yalnız `status='ACTIVE'` satırlar arasında. Koşulsuz
   tekillik, satılmış bir hayvanın numarasının bir daha girilememesine yol
   açardı.

--- YAPISAL KARAR: DOĞUM İKİ ŞEYDİR --------------------------------------

Bir doğum hem annenin geçmişinde bir OLAY, hem yeni bir HAYVAN kaydıdır.
``animal_births`` olayı tutar, ``animals.mother_id`` bağı kurar. Tek tabloda
tutsaydık ya yavru kaydı kaybolurdu (doğum satırı hayvan değil) ya doğum
geçmişi (yavru satırında "kaçıncı doğum, güçlük derecesi" yeri yok).

--- KÜÇÜKBAŞ FARKI --------------------------------------------------------

Koyun/keçi pratikte SÜRÜ olarak yönetilir; her hayvanı tek tek küpe numarasıyla
izlemek zorunlu tutulamaz. Bu yüzden ``animals.ear_tag`` NULLABLE ve
``animal_groups`` bağımsız bir birim: bireysel kayıt tutulmayan sürülerde
sayım grup üzerinden yürür.

--- ÇAPRAZ KİRACI ---------------------------------------------------------

Yeni tablolar arası ilişkiler BİLEŞİK yabancı anahtarla (company_id, id)
bağlanıyor: B firmasının hayvanını A firmasının sürüsüne bağlamak veritabanı
seviyesinde imkânsız. `app_users` ve `machines` gibi MEVCUT tablolara düz FK
kullanılıyor — onlara bileşik anahtar eklemek SQLite'ta tablo yeniden
oluşturmayı gerektirir ve mevcut veriyi riske atar; o bağların kiracı kontrolü
uygulama katmanında yapılıyor (tarla modülünde de aynı gerekçe).
"""
from alembic import op
import sqlalchemy as sa

revision = "20260808_0049"
down_revision = "20260807_0048"
branch_labels = None
depends_on = None

#: Miktar (süt litresi, canlı ağırlık, yem) ve tutar. Tarla modülüyle aynı
#: hassasiyet: float ASLA kullanılmıyor.
MIKTAR = sa.Numeric(18, 4)
TUTAR = sa.Numeric(18, 2)

SPECIES = ("CATTLE", "BUFFALO", "SHEEP", "GOAT")
SEX = ("FEMALE", "MALE")
#: Silme yerine yaşam döngüsü: geçmiş verim ve maliyet korunur.
ANIMAL_STATUS = ("ACTIVE", "SOLD", "DEAD", "SLAUGHTERED", "ARCHIVED")
ACQUISITION = ("BORN", "PURCHASED", "GIFTED", "OTHER")
BREEDING_METHOD = ("AI", "NATURAL", "EMBRYO_TRANSFER")
BREEDING_RESULT = ("UNKNOWN", "PREGNANT", "NOT_PREGNANT", "ABORTED")
BIRTH_OUTCOME = ("LIVE", "STILLBORN", "ABORTION")
MOVEMENT_KIND = ("PURCHASE", "SALE", "DEATH", "SLAUGHTER", "TRANSFER_IN", "TRANSFER_OUT")


def _zaman_sutunlari() -> list:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _liste_kisiti(sutun: str, degerler: tuple, ad: str) -> sa.CheckConstraint:
    icerik = "','".join(degerler)
    return sa.CheckConstraint(f"{sutun} IN ('{icerik}')", name=ad)


def upgrade() -> None:
    bind = op.get_bind()
    denetci = sa.inspect(bind)
    mevcut = set(denetci.get_table_names())

    if "animal_groups" not in mevcut:
        op.create_table(
            "animal_groups",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=40), nullable=False),
            sa.Column("name", sa.String(length=180), nullable=False),
            # Sürü/padok/ağıl. Küçükbaşta ASIL birim: bireysel kayıt
            # tutulmayan sürülerde sayım buradan yürür.
            sa.Column("species", sa.String(length=20), nullable=True),
            sa.Column("location", sa.String(length=180), nullable=True),
            # Bireysel kayıt tutulmayan sürüde baş sayısı. Bireysel kayıt
            # tutuluyorsa bu alan BOŞ kalır ve sayım `animals`tan gelir; iki
            # kaynağı aynı anda doldurmak çifte sayıma yol açar.
            sa.Column("head_count", sa.Integer(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
            *_zaman_sutunlari(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_animal_groups_company"),
            sa.UniqueConstraint("company_id", "code", name="uq_animal_groups_code"),
            # Bileşik anahtar: alt tabloların çapraz kiracı bağ kurmasını
            # veritabanı seviyesinde imkânsız kılar.
            sa.UniqueConstraint("company_id", "id", name="uq_animal_groups_company_id"),
            sa.CheckConstraint("head_count IS NULL OR head_count >= 0", name="ck_animal_groups_head_count"),
            _liste_kisiti("species", SPECIES + ("MIXED",), "ck_animal_groups_species"),
        )

    if "animals" not in mevcut:
        op.create_table(
            "animals",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            # KÜPE NUMARASI — hayvanın yasal kimliği. NULLABLE: küçükbaş
            # sürülerinde bireysel küpe kaydı tutulmayabilir (bkz. modül
            # başlığı). Biçim kısıtı BİLEREK YOK: küpe standardı şu an geçişte.
            sa.Column("ear_tag", sa.String(length=32), nullable=True),
            sa.Column("name", sa.String(length=120), nullable=True),
            sa.Column("species", sa.String(length=20), nullable=False),
            sa.Column("breed", sa.String(length=120), nullable=True),
            sa.Column("sex", sa.String(length=10), nullable=False),
            sa.Column("birth_date", sa.Date(), nullable=True),
            sa.Column("acquisition", sa.String(length=20), nullable=False, server_default="BORN"),
            sa.Column("acquired_on", sa.Date(), nullable=True),
            sa.Column("group_id", sa.Integer(), nullable=True),
            # Soy bağı. Kendine referans; bileşik anahtarla kiracı içinde kilitli.
            sa.Column("mother_id", sa.Integer(), nullable=True),
            sa.Column("father_id", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
            sa.Column("notes", sa.Text(), nullable=True),
            *_zaman_sutunlari(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_animals_company"),
            sa.ForeignKeyConstraint(
                ["company_id", "group_id"],
                ["animal_groups.company_id", "animal_groups.id"],
                name="fk_animals_group_same_company",
            ),
            sa.UniqueConstraint("company_id", "id", name="uq_animals_company_id"),
            _liste_kisiti("species", SPECIES, "ck_animals_species"),
            _liste_kisiti("sex", SEX, "ck_animals_sex"),
            _liste_kisiti("status", ANIMAL_STATUS, "ck_animals_status"),
            _liste_kisiti("acquisition", ACQUISITION, "ck_animals_acquisition"),
        )
        # KÜPE TEKİLLİĞİ KOŞULLU. Küpe numarası yeniden kullanılabiliyor
        # (hayvan satılır/ölür, numara döner). Koşulsuz tekillik, satılmış bir
        # hayvanın numarasının bir daha girilememesine yol açardı.
        op.create_index(
            "uq_animals_active_ear_tag",
            "animals",
            ["company_id", "ear_tag"],
            unique=True,
            sqlite_where=sa.text("ear_tag IS NOT NULL AND status = 'ACTIVE'"),
            postgresql_where=sa.text("ear_tag IS NOT NULL AND status = 'ACTIVE'"),
        )
        op.create_index("ix_animals_company_status", "animals", ["company_id", "status"])
        op.create_index("ix_animals_company_mother", "animals", ["company_id", "mother_id"])

    if "animal_vaccinations" not in mevcut:
        op.create_table(
            "animal_vaccinations",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("animal_id", sa.Integer(), nullable=False),
            # Şap ve brusella Bakanlıkça ZORUNLU; liste serbest metin çünkü
            # zorunlu olmayan aşılar (septisemi, IBR...) işletmeye göre değişir
            # ve kapalı bir liste kullanıcıyı kaydını tutamaz hâle getirirdi.
            sa.Column("vaccine", sa.String(length=120), nullable=False),
            sa.Column("applied_on", sa.Date(), nullable=False),
            # Kaçıncı doz. Şapta ilk iki doz bir ay arayla, sonra 4-6 ayda bir;
            # brusellada ikinci doz 4-12 ay sonra. Sıra bilinmeden "aşısı tam
            # mı" sorusu cevaplanamaz.
            sa.Column("dose_no", sa.Integer(), nullable=True),
            # Bir SONRAKİ dozun beklendiği tarih. Sunucu hesaplayacak (FAZ 4);
            # burada saklanması, hesabın o günün kuralına göre yapılmış hâlini
            # korumak için — kural değişirse geçmiş kayıt geriye dönük kaymaz.
            sa.Column("next_due_on", sa.Date(), nullable=True),
            sa.Column("veterinarian", sa.String(length=180), nullable=True),
            sa.Column("batch_no", sa.String(length=60), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="RECORDED"),
            *_zaman_sutunlari(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_animal_vacc_company"),
            sa.ForeignKeyConstraint(
                ["company_id", "animal_id"],
                ["animals.company_id", "animals.id"],
                name="fk_animal_vacc_animal_same_company",
            ),
            sa.UniqueConstraint("company_id", "id", name="uq_animal_vacc_company_id"),
            sa.CheckConstraint("dose_no IS NULL OR dose_no > 0", name="ck_animal_vacc_dose_no"),
        )
        op.create_index("ix_animal_vacc_company_animal", "animal_vaccinations", ["company_id", "animal_id"])
        op.create_index("ix_animal_vacc_company_due", "animal_vaccinations", ["company_id", "next_due_on"])

    if "animal_breedings" not in mevcut:
        op.create_table(
            "animal_breedings",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            # Tohumlanan DİŞİ. Uygulama katmanı cinsiyeti doğrular.
            sa.Column("animal_id", sa.Integer(), nullable=False),
            sa.Column("bred_on", sa.Date(), nullable=False),
            sa.Column("method", sa.String(length=20), nullable=False, server_default="AI"),
            # Suni tohumlamada sperma/boğa kimliği serbest metin; tabii aşımda
            # kendi sürümüzdeki boğa olabilir, o yüzden ikisi ayrı alan.
            sa.Column("sire_code", sa.String(length=120), nullable=True),
            sa.Column("sire_animal_id", sa.Integer(), nullable=True),
            sa.Column("technician", sa.String(length=180), nullable=True),
            sa.Column("result", sa.String(length=20), nullable=False, server_default="UNKNOWN"),
            sa.Column("checked_on", sa.Date(), nullable=True),
            # Beklenen doğum. SABİT GEBELİK SÜRESİ YAZILMIYOR: güvenilir
            # kaynak bulunamadı ve tür/ırk değişkenlik gösteriyor. Firma
            # ayarından hesaplanıp buraya yazılacak (FAZ 5).
            sa.Column("expected_birth_on", sa.Date(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="RECORDED"),
            *_zaman_sutunlari(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_animal_breed_company"),
            sa.ForeignKeyConstraint(
                ["company_id", "animal_id"],
                ["animals.company_id", "animals.id"],
                name="fk_animal_breed_animal_same_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "sire_animal_id"],
                ["animals.company_id", "animals.id"],
                name="fk_animal_breed_sire_same_company",
            ),
            sa.UniqueConstraint("company_id", "id", name="uq_animal_breed_company_id"),
            _liste_kisiti("method", BREEDING_METHOD, "ck_animal_breed_method"),
            _liste_kisiti("result", BREEDING_RESULT, "ck_animal_breed_result"),
        )
        op.create_index("ix_animal_breed_company_animal", "animal_breedings", ["company_id", "animal_id"])

    if "animal_births" not in mevcut:
        op.create_table(
            "animal_births",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("mother_id", sa.Integer(), nullable=False),
            # Hangi tohumlamanın sonucu. Boş olabilir: geçmiş kayıt girilirken
            # tohumlama bilinmeyebilir. Zorunlu tutmak, eski verinin hiç
            # girilememesine yol açardı.
            sa.Column("breeding_id", sa.Integer(), nullable=True),
            sa.Column("birth_date", sa.Date(), nullable=False),
            sa.Column("outcome", sa.String(length=20), nullable=False, server_default="LIVE"),
            # Doğum güçlüğü 1-4 arası derecelendiriliyor; sürü sağlığı
            # göstergesi olarak izleniyor.
            sa.Column("difficulty", sa.Integer(), nullable=True),
            # Kaç yavru. İkiz/üçüz küçükbaşta yaygın. Bireysel kayıt
            # tutulmayan sürülerde yavru satırı açılmaz, sayı burada durur.
            sa.Column("offspring_count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="RECORDED"),
            *_zaman_sutunlari(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_animal_births_company"),
            sa.ForeignKeyConstraint(
                ["company_id", "mother_id"],
                ["animals.company_id", "animals.id"],
                name="fk_animal_births_mother_same_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "breeding_id"],
                ["animal_breedings.company_id", "animal_breedings.id"],
                name="fk_animal_births_breeding_same_company",
            ),
            sa.UniqueConstraint("company_id", "id", name="uq_animal_births_company_id"),
            _liste_kisiti("outcome", BIRTH_OUTCOME, "ck_animal_births_outcome"),
            sa.CheckConstraint(
                "difficulty IS NULL OR (difficulty BETWEEN 1 AND 4)",
                name="ck_animal_births_difficulty",
            ),
            sa.CheckConstraint("offspring_count >= 0", name="ck_animal_births_offspring"),
        )
        op.create_index("ix_animal_births_company_mother", "animal_births", ["company_id", "mother_id"])
        op.create_index("ix_animal_births_company_date", "animal_births", ["company_id", "birth_date"])

    if "animal_weights" not in mevcut:
        op.create_table(
            "animal_weights",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("animal_id", sa.Integer(), nullable=False),
            sa.Column("weighed_on", sa.Date(), nullable=False),
            # Kilogram. NUMERIC — besi tarafında günlük canlı ağırlık artışı
            # bu değerlerden türetiliyor; float yuvarlaması artışı bozar.
            sa.Column("weight_kg", MIKTAR, nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            *_zaman_sutunlari(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_animal_weights_company"),
            sa.ForeignKeyConstraint(
                ["company_id", "animal_id"],
                ["animals.company_id", "animals.id"],
                name="fk_animal_weights_animal_same_company",
            ),
            sa.UniqueConstraint("company_id", "id", name="uq_animal_weights_company_id"),
            sa.CheckConstraint("weight_kg > 0", name="ck_animal_weights_positive"),
        )
        op.create_index("ix_animal_weights_company_animal", "animal_weights", ["company_id", "animal_id"])

    if "milk_yields" not in mevcut:
        op.create_table(
            "milk_yields",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            # Bireysel sağım kaydı. Grup bazlı toplam sağım için `group_id`
            # kullanılır; ikisinden BİRİ dolu olur (uygulama katmanı zorlar).
            sa.Column("animal_id", sa.Integer(), nullable=True),
            sa.Column("group_id", sa.Integer(), nullable=True),
            sa.Column("milked_on", sa.Date(), nullable=False),
            # Sabah/akşam ayrımı: günlük toplamı iki kez saymamak için sağım
            # oturumu ayrı tutuluyor.
            sa.Column("session", sa.String(length=20), nullable=True),
            sa.Column("quantity_liters", MIKTAR, nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            *_zaman_sutunlari(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_milk_yields_company"),
            sa.ForeignKeyConstraint(
                ["company_id", "animal_id"],
                ["animals.company_id", "animals.id"],
                name="fk_milk_yields_animal_same_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "group_id"],
                ["animal_groups.company_id", "animal_groups.id"],
                name="fk_milk_yields_group_same_company",
            ),
            sa.UniqueConstraint("company_id", "id", name="uq_milk_yields_company_id"),
            sa.CheckConstraint("quantity_liters > 0", name="ck_milk_yields_positive"),
            # Hayvan ya da grup — biri dolu olmalı, ikisi birden değil.
            # İkisi de doluysa aynı süt iki kez sayılır.
            sa.CheckConstraint(
                "(animal_id IS NOT NULL AND group_id IS NULL) OR "
                "(animal_id IS NULL AND group_id IS NOT NULL)",
                name="ck_milk_yields_animal_xor_group",
            ),
        )
        op.create_index("ix_milk_yields_company_date", "milk_yields", ["company_id", "milked_on"])

    if "animal_movements" not in mevcut:
        op.create_table(
            "animal_movements",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("animal_id", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(length=20), nullable=False),
            sa.Column("moved_on", sa.Date(), nullable=False),
            # Tutar ELLE giriliyor; V1 muhasebeye fiş yazmıyor (ürün sınırı).
            sa.Column("amount", TUTAR, nullable=True),
            sa.Column("counterparty", sa.String(length=180), nullable=True),
            sa.Column("reason", sa.String(length=255), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            *_zaman_sutunlari(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_animal_moves_company"),
            sa.ForeignKeyConstraint(
                ["company_id", "animal_id"],
                ["animals.company_id", "animals.id"],
                name="fk_animal_moves_animal_same_company",
            ),
            sa.UniqueConstraint("company_id", "id", name="uq_animal_moves_company_id"),
            _liste_kisiti("kind", MOVEMENT_KIND, "ck_animal_moves_kind"),
            sa.CheckConstraint("amount IS NULL OR amount >= 0", name="ck_animal_moves_amount"),
        )
        op.create_index("ix_animal_moves_company_animal", "animal_movements", ["company_id", "animal_id"])

    if "herd_integration_events" not in mevcut:
        # Tarla modülündeki `field_integration_events` ile aynı gerekçe:
        # ileriki stok/muhasebe entegrasyonu için kaynak belge kimliği ve
        # idempotency sözleşmesi HAZIR duruyor. V1'de yazan bir üretici YOK;
        # yazmak ürün sınırını (fiş yazmama) aşardı.
        op.create_table(
            "herd_integration_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("source_type", sa.String(length=60), nullable=False),
            sa.Column("source_id", sa.Integer(), nullable=False),
            sa.Column("target", sa.String(length=60), nullable=False),
            sa.Column("idempotency_key", sa.String(length=120), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
            sa.Column("error", sa.Text(), nullable=True),
            *_zaman_sutunlari(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_herd_events_company"),
            sa.UniqueConstraint(
                "company_id", "idempotency_key", name="uq_herd_events_idempotency"
            ),
        )


def downgrade() -> None:
    for tablo in (
        "herd_integration_events",
        "animal_movements",
        "milk_yields",
        "animal_weights",
        "animal_births",
        "animal_breedings",
        "animal_vaccinations",
        "animals",
        "animal_groups",
    ):
        op.drop_table(tablo)
