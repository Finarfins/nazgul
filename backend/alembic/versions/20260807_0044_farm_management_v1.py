"""Tarla Yönetimi V1 — FAZ 1: veri modeli.

Revision ID: 20260807_0044
Revises: 20260807_0043

Konu: mobil-erp#2 (kaynak: sungur-tarim-erp#201).

V1 GÜVENİLİR BİR TARIMSAL OPERASYON ALT DEFTERİDİR. Stok ve muhasebeye fiş
YAZMAZ. Bu bilinçli: tarla faaliyetlerinin stok hareketine dönüşmesi ayrı bir
karar zinciri (hangi depodan, hangi maliyetle, hangi anda) ve onu doğru
kurmadan yazmak, düzeltilmesi zor kayıtlar üretir. Bunun yerine
``field_integration_events`` bir OUTBOX olarak şimdiden açılıyor: FAZ 4'te
tüketici yazıldığında geçmiş faaliyetler de yeniden işlenebilir olacak.

TASARIM KARARLARI (şemaya gömülü olanlar):

* **Silme yok, yaşam döngüsü var.** Her iş tablosunda ``status`` sütunu var ve
  iptal bir durumdur. Sebep: sezon maliyeti ve dekar başına verim geçmişe
  dönük raporlanıyor; bir faaliyeti silmek geçmiş bir sezonun maliyetini
  sessizce değiştirirdi.
* **Alan ve miktarlar NUMERIC.** Dekar hesapları doğrudan verim ve maliyet
  bölmelerine giriyor; float yuvarlaması dekar başına maliyeti kaydırır.
  Depodaki mevcut kural izlendi: miktar (18,4), tutar (18,2).
* **Her tekillik kiracı kapsamında.** ``UniqueConstraint`` listelerinin hepsi
  ``company_id`` ile başlıyor; iki firmanın aynı parsel kodunu kullanabilmesi
  gerekiyor.
* **Çapraz kiracı ilişki kurulamamalı.** Yabancı anahtarlar tek başına bunu
  engellemez (``farm_parcels.farm_id`` başka firmanın çiftliğini gösterebilir).
  Şemadaki koruma BİLEŞİK yabancı anahtar: çocuk tablo ``(company_id, id)``
  çiftine bağlanıyor, böylece farklı firmanın satırına bağlanmak veritabanı
  seviyesinde imkânsız. Bunun için ebeveyn tablolara ``(company_id, id)``
  benzersizliği eklendi — teknik olarak gereksiz görünür (id zaten benzersiz)
  ama bileşik FK'nin hedefi olması için ŞART.
* **Toplam maliyet sunucuda türetilir.** Şema hem birim maliyeti hem toplamı
  saklıyor; toplamın istemciden alınmaması uygulama katmanının işi (FAZ 2),
  ama sütun burada duruyor ki rapor sorguları satır satır çarpmak zorunda
  kalmasın.
"""

from alembic import op
import sqlalchemy as sa

revision = "20260807_0044"
down_revision = "20260807_0043"
branch_labels = None
depends_on = None

# Miktar/alan: dekar ve litre gibi bölünebilir büyüklükler 4 hane istiyor.
MIKTAR = sa.Numeric(18, 4)
# Para: depodaki para kuralıyla aynı.
TUTAR = sa.Numeric(18, 2)

_TABLOLAR = (
    "field_integration_events",
    "field_tasks",
    "field_harvests",
    "field_activity_inputs",
    "field_activities",
    "crop_seasons",
    "farm_parcels",
    "farms",
)


def _zaman_sutunlari() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    mevcut = set(sa.inspect(bind).get_table_names())

    # --- çiftlik ------------------------------------------------------------
    if "farms" not in mevcut:
        op.create_table(
            "farms",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=40), nullable=False),
            sa.Column("name", sa.String(length=180), nullable=False),
            # Çiftliğin sahibi bir cari olabilir (kendi işletmemiz ise NULL).
            sa.Column("customer_id", sa.Integer(), nullable=True),
            sa.Column("city", sa.String(length=80), nullable=True),
            sa.Column("district", sa.String(length=80), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
            *_zaman_sutunlari(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
            sa.UniqueConstraint("company_id", "code", name="uq_farms_company_code"),
            # Bileşik FK hedefi (bkz. başlıktaki çapraz-kiracı gerekçesi).
            sa.UniqueConstraint("company_id", "id", name="uq_farms_company_id"),
        )
        op.create_index("ix_farms_company_status", "farms", ["company_id", "status"])

    # --- parsel -------------------------------------------------------------
    if "farm_parcels" not in mevcut:
        op.create_table(
            "farm_parcels",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("farm_id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=40), nullable=False),
            sa.Column("name", sa.String(length=180), nullable=False),
            # Alan DEKAR cinsinden. Birim sütunu yok: tek birime sabitlemek,
            # her verim/maliyet bölmesinde birim çevirmekten güvenli.
            sa.Column("area_decare", MIKTAR, nullable=False),
            # Tapu bilgisi; kadastro kaydıyla eşleştirme için.
            sa.Column("parcel_no", sa.String(length=40), nullable=True),
            sa.Column("block_no", sa.String(length=40), nullable=True),
            sa.Column("city", sa.String(length=80), nullable=True),
            sa.Column("district", sa.String(length=80), nullable=True),
            sa.Column("neighborhood", sa.String(length=120), nullable=True),
            # Sınır poligonu GeoJSON metni olarak. PostGIS'e bağımlılık
            # BİLEREK yok: V1 haritada çizim yapmıyor, yalnız saklıyor.
            sa.Column("boundary_geojson", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
            *_zaman_sutunlari(),
            sa.CheckConstraint("area_decare > 0", name="ck_farm_parcels_area_positive"),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(
                ["company_id", "farm_id"], ["farms.company_id", "farms.id"],
                name="fk_farm_parcels_farm_same_company",
            ),
            sa.UniqueConstraint("company_id", "code", name="uq_farm_parcels_company_code"),
            sa.UniqueConstraint("company_id", "id", name="uq_farm_parcels_company_id"),
        )
        op.create_index("ix_farm_parcels_company_farm", "farm_parcels", ["company_id", "farm_id"])

    # --- sezon --------------------------------------------------------------
    if "crop_seasons" not in mevcut:
        op.create_table(
            "crop_seasons",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("parcel_id", sa.Integer(), nullable=False),
            sa.Column("season_year", sa.Integer(), nullable=False),
            sa.Column("crop", sa.String(length=120), nullable=False),
            sa.Column("variety", sa.String(length=120), nullable=True),
            sa.Column("started_on", sa.Date(), nullable=True),
            sa.Column("ended_on", sa.Date(), nullable=True),
            # PLANNED | ACTIVE | HARVESTED | CLOSED | CANCELLED
            sa.Column("status", sa.String(length=20), nullable=False, server_default="PLANNED"),
            # Bu sezonda fiilen ekilen alan; parselin tamamı ekilmemiş olabilir.
            sa.Column("planted_area_decare", MIKTAR, nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            *_zaman_sutunlari(),
            sa.CheckConstraint(
                "planted_area_decare IS NULL OR planted_area_decare > 0",
                name="ck_crop_seasons_planted_area_positive",
            ),
            # Sezon bitişi başlangıcından önce olamaz.
            sa.CheckConstraint(
                "started_on IS NULL OR ended_on IS NULL OR ended_on >= started_on",
                name="ck_crop_seasons_date_order",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(
                ["company_id", "parcel_id"], ["farm_parcels.company_id", "farm_parcels.id"],
                name="fk_crop_seasons_parcel_same_company",
            ),
            sa.UniqueConstraint("company_id", "id", name="uq_crop_seasons_company_id"),
        )
        op.create_index(
            "ix_crop_seasons_company_parcel_year",
            "crop_seasons", ["company_id", "parcel_id", "season_year"],
        )

    # --- faaliyet -----------------------------------------------------------
    if "field_activities" not in mevcut:
        op.create_table(
            "field_activities",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("season_id", sa.Integer(), nullable=False),
            # SOWING | FERTILIZING | SPRAYING | IRRIGATION | TILLAGE | OTHER
            sa.Column("activity_type", sa.String(length=30), nullable=False),
            sa.Column("performed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("applied_area_decare", MIKTAR, nullable=True),
            sa.Column("operator_user_id", sa.Integer(), nullable=True),
            sa.Column("machine_id", sa.Integer(), nullable=True),
            # İlaçlama için güvenlik aralıkları. GÜN cinsinden ve negatif olamaz;
            # hasat bekleme süresi yasal bir kısıt, yanlış kaydı gerçek risk.
            sa.Column("reentry_interval_days", sa.Integer(), nullable=True),
            sa.Column("preharvest_interval_days", sa.Integer(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            # Faaliyet alanı parseli aşarsa açık gerekçe istenir (uygulama
            # katmanı zorlar); gerekçe burada saklanır ki denetimde görünsün.
            sa.Column("area_override_reason", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="RECORDED"),
            *_zaman_sutunlari(),
            sa.CheckConstraint(
                "applied_area_decare IS NULL OR applied_area_decare > 0",
                name="ck_field_activities_area_positive",
            ),
            sa.CheckConstraint(
                "reentry_interval_days IS NULL OR reentry_interval_days >= 0",
                name="ck_field_activities_reentry_nonnegative",
            ),
            sa.CheckConstraint(
                "preharvest_interval_days IS NULL OR preharvest_interval_days >= 0",
                name="ck_field_activities_preharvest_nonnegative",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(
                ["company_id", "season_id"], ["crop_seasons.company_id", "crop_seasons.id"],
                name="fk_field_activities_season_same_company",
            ),
            sa.ForeignKeyConstraint(["operator_user_id"], ["app_users.id"]),
            # MEVCUT tablolara BİLEŞİK FK atılmadı. Bunun için machines'a
            # (company_id, id) benzersizliği eklemek gerekirdi; SQLite'ta bu
            # tablonun YENİDEN İNŞASI demek ve üretimde duran bir tabloyu yeni
            # bir özellik için riske atmak doğru değil. Deponun mevcut deseni
            # zaten uygulama katmanında doğrulama (bkz. work_order_parts
            # ``_validate_references``); aynı yol izleniyor ve FAZ 2 uçları
            # makinenin aynı firmaya ait olduğunu kontrol edecek.
            sa.ForeignKeyConstraint(["machine_id"], ["machines.id"]),
            sa.UniqueConstraint("company_id", "id", name="uq_field_activities_company_id"),
        )
        op.create_index(
            "ix_field_activities_company_season_date",
            "field_activities", ["company_id", "season_id", "performed_at"],
        )

    # --- faaliyet girdileri -------------------------------------------------
    if "field_activity_inputs" not in mevcut:
        op.create_table(
            "field_activity_inputs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("activity_id", sa.Integer(), nullable=False),
            # Girdi ya stok ürününe bağlıdır ya serbest metindir: çiftçi
            # kendi ürettiği gübreyi de kaydedebilmeli.
            sa.Column("product_id", sa.Integer(), nullable=True),
            sa.Column("input_name", sa.String(length=180), nullable=False),
            sa.Column("quantity", MIKTAR, nullable=False),
            sa.Column("unit", sa.String(length=32), nullable=False),
            sa.Column("unit_cost", TUTAR, nullable=True),
            # Sunucuda türetilir; istemciden kabul edilmez (bkz. başlık).
            sa.Column("total_cost", TUTAR, nullable=True),
            # İlaç dozu ve birimi: "100 ml/dekar" gibi.
            sa.Column("dose", MIKTAR, nullable=True),
            sa.Column("dose_unit", sa.String(length=32), nullable=True),
            *_zaman_sutunlari(),
            sa.CheckConstraint("quantity > 0", name="ck_field_activity_inputs_qty_positive"),
            sa.CheckConstraint(
                "unit_cost IS NULL OR unit_cost >= 0",
                name="ck_field_activity_inputs_unit_cost_nonnegative",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(
                ["company_id", "activity_id"],
                ["field_activities.company_id", "field_activities.id"],
                name="fk_field_activity_inputs_activity_same_company",
            ),
            # Aynı gerekçe (bkz. field_activities.machine_id): mevcut products
            # tablosuna kısıt eklemek yerine uygulama katmanı doğrulaması.
            sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
            sa.UniqueConstraint("company_id", "id", name="uq_field_activity_inputs_company_id"),
        )
        op.create_index(
            "ix_field_activity_inputs_company_activity",
            "field_activity_inputs", ["company_id", "activity_id"],
        )

    # --- hasat --------------------------------------------------------------
    if "field_harvests" not in mevcut:
        op.create_table(
            "field_harvests",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("season_id", sa.Integer(), nullable=False),
            sa.Column("harvested_on", sa.Date(), nullable=False),
            sa.Column("quantity", MIKTAR, nullable=False),
            sa.Column("unit", sa.String(length=32), nullable=False),
            sa.Column("harvested_area_decare", MIKTAR, nullable=True),
            sa.Column("quality_grade", sa.String(length=60), nullable=True),
            # Nem yüzdesi; alım fiyatını doğrudan etkiliyor.
            sa.Column("moisture_percent", sa.Numeric(5, 2), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="RECORDED"),
            *_zaman_sutunlari(),
            sa.CheckConstraint("quantity > 0", name="ck_field_harvests_qty_positive"),
            sa.CheckConstraint(
                "harvested_area_decare IS NULL OR harvested_area_decare > 0",
                name="ck_field_harvests_area_positive",
            ),
            sa.CheckConstraint(
                "moisture_percent IS NULL OR (moisture_percent >= 0 AND moisture_percent <= 100)",
                name="ck_field_harvests_moisture_range",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(
                ["company_id", "season_id"], ["crop_seasons.company_id", "crop_seasons.id"],
                name="fk_field_harvests_season_same_company",
            ),
            sa.UniqueConstraint("company_id", "id", name="uq_field_harvests_company_id"),
        )
        op.create_index(
            "ix_field_harvests_company_season", "field_harvests", ["company_id", "season_id"]
        )

    # --- görev --------------------------------------------------------------
    if "field_tasks" not in mevcut:
        op.create_table(
            "field_tasks",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("season_id", sa.Integer(), nullable=True),
            sa.Column("parcel_id", sa.Integer(), nullable=True),
            sa.Column("title", sa.String(length=180), nullable=False),
            sa.Column("due_date", sa.Date(), nullable=True),
            # OPEN | DONE | CANCELLED
            sa.Column("status", sa.String(length=20), nullable=False, server_default="OPEN"),
            sa.Column("priority", sa.String(length=20), nullable=False, server_default="NORMAL"),
            sa.Column("assigned_user_id", sa.Integer(), nullable=True),
            # Görev bir faaliyete dönüştüğünde bağlanır: "yapıldı" bilgisi
            # kaydın kendisinden gelsin, ayrı bir işaretten değil.
            sa.Column("activity_id", sa.Integer(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            *_zaman_sutunlari(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(
                ["company_id", "season_id"], ["crop_seasons.company_id", "crop_seasons.id"],
                name="fk_field_tasks_season_same_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "parcel_id"], ["farm_parcels.company_id", "farm_parcels.id"],
                name="fk_field_tasks_parcel_same_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "activity_id"],
                ["field_activities.company_id", "field_activities.id"],
                name="fk_field_tasks_activity_same_company",
            ),
            sa.ForeignKeyConstraint(["assigned_user_id"], ["app_users.id"]),
            sa.UniqueConstraint("company_id", "id", name="uq_field_tasks_company_id"),
        )
        op.create_index(
            "ix_field_tasks_company_status_due",
            "field_tasks", ["company_id", "status", "due_date"],
        )

    # --- entegrasyon outbox -------------------------------------------------
    if "field_integration_events" not in mevcut:
        op.create_table(
            "field_integration_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            # Kaynak: hangi tarla kaydı bu olayı doğurdu.
            sa.Column("source_type", sa.String(length=40), nullable=False),
            sa.Column("source_id", sa.Integer(), nullable=False),
            # Hedef: stok mu, muhasebe mi (V1'de tüketici YOK).
            sa.Column("target", sa.String(length=40), nullable=False),
            sa.Column("idempotency_key", sa.String(length=120), nullable=False),
            # PENDING | SENT | FAILED | SKIPPED
            sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            *_zaman_sutunlari(),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            # Tekrar çalıştırmanın ikinci bir fiş yazmamasını sağlayan kısıt.
            # Uygulama katmanındaki kontrol yarışa açıktır; benzersizlik
            # veritabanında olmak zorunda.
            sa.UniqueConstraint(
                "company_id", "idempotency_key", name="uq_field_integration_events_key"
            ),
        )
        op.create_index(
            "ix_field_integration_events_company_status",
            "field_integration_events", ["company_id", "status"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    mevcut = set(sa.inspect(bind).get_table_names())
    # Yabancı anahtar bağımlılığının TERSİ sırada düşürülüyor.
    for tablo in _TABLOLAR:
        if tablo in mevcut:
            op.drop_table(tablo)
