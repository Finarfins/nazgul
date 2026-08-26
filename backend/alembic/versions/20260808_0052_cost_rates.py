"""Gerçek Maliyet V1 — FAZ 1: saatlik maliyet oranları.

Revision ID: 20260808_0052
Revises: 20260808_0051

Konu: mobil-erp#24.

--- NEDEN AYRI TABLO, NEDEN `machines`e SÜTUN DEĞİL --------------------------

Makine saatlik maliyetini `machines` tablosuna sütun olarak eklemek ilk bakışta
daha basit görünüyor. Eklemedik, iki sebeple:

1. `machines` BAYİNİN MÜŞTERİ MAKİNELERİ tablosu. Oradaki satırların çoğu
   müşteriye ait; onlara "bizim saatlik maliyetimiz" yazmak alan anlamını
   bozardı ve müşteri makinesine maliyet girilmesi anlamsız olurdu.

2. İŞÇİLİK oranının makinesi yok. Tek tabloda toplamak, iki farklı şeyi
   (kim çalıştı, hangi makine çalıştı) aynı desenle yönetmeyi sağlıyor.

--- NEDEN GEÇERLİLİK TARİHİ YOK -------------------------------------------

Oranlar zaman içinde değişir; klasik çözüm geçerlilik tarihli oran tablosudur.
Onun yerine bu depoda ZATEN ÇALIŞAN deseni kullanıyoruz: oran, kayıt
oluşturulurken İŞLEM SATIRINA KOPYALANIR (bkz. `work_order_labor_lines`:
"Rate freeze. `hourly_rate` is written when the line is created ... the stored
rate is the only rate billing ever reads").

Bu tabloda tutulan şey bu yüzden bir GEÇMİŞ değil, YALNIZ BUGÜNKÜ VARSAYILAN.
Geçmiş, işlem satırlarının kendisinde duruyor. Tarih aralıklı bir oran tablosu
eklemek, aynı bilgiyi iki yerde tutup ikisinin ayrışmasına kapı açardı.

--- KİME AİT: MAKİNE, KULLANICI YA DA GENEL --------------------------------

Bir oran satırı üç şeyden birine bağlanır:
  * `machine_id` dolu  → o makinenin saatlik maliyeti
  * `user_id` dolu     → o kişinin saatlik işçilik maliyeti
  * ikisi de boş       → o TÜRÜN firma geneli varsayılanı

Kısıt bunu veritabanı seviyesinde zorluyor: ikisi birden dolu olamaz ve tür
ile bağ tutarlı olmalı (MACHINE türünde `user_id` olamaz). Uygulama katmanına
bırakılsaydı tek bir unutulmuş kontrol, hangi orana bakılacağı belirsiz bir
satır üretirdi.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260808_0052"
down_revision = "20260808_0051"
branch_labels = None
depends_on = None

TUTAR = sa.Numeric(18, 2)


def upgrade() -> None:
    bind = op.get_bind()
    mevcut = set(sa.inspect(bind).get_table_names())

    if "cost_rates" not in mevcut:
        op.create_table(
            "cost_rates",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            # LABOR | MACHINE
            sa.Column("kind", sa.String(length=20), nullable=False),
            sa.Column("machine_id", sa.Integer(), nullable=True),
            sa.Column("user_id", sa.Integer(), nullable=True),
            # Etiket: "Traktör 1", "Mevsimlik işçi", "Sürücü". Oran satırı
            # makineye/kullanıcıya bağlı olmayabilir (firma geneli) ve o zaman
            # kullanıcıya gösterilecek tek şey bu ad.
            sa.Column("label", sa.String(length=180), nullable=False),
            sa.Column("hourly_rate", TUTAR, nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_cost_rates_company"),
            # MEVCUT tablolara BİLEŞİK FK ATILMIYOR — tarla FAZ 1'de verilen
            # kararın aynısı (bkz. migration 0044, `field_activities.machine_id`):
            # `machines`e (company_id, id) benzersizliği eklemek SQLite'ta
            # tablonun YENİDEN İNŞASI demek ve üretimde duran bir tabloyu yeni
            # bir özellik için riske atmak doğru değil. Çapraz kiracı bağ
            # UYGULAMA KATMANINDA engelleniyor (uç, makinenin/kullanıcının aynı
            # firmaya ait olduğunu doğruluyor) ve testle çivileniyor.
            sa.ForeignKeyConstraint(["machine_id"], ["machines.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["app_users.id"]),
            sa.UniqueConstraint("company_id", "id", name="uq_cost_rates_company_id"),
            # ORAN SIFIR OLABİLİR ama negatif olamaz: kendi arazisinde kendi
            # traktörüyle çalışan bir işletme "makine maliyeti 0" demek
            # isteyebilir ve bu geçerli bir cevaptır. Negatif oran ise her
            # zaman veri hatasıdır ve toplam maliyeti sessizce düşürürdü.
            sa.CheckConstraint("hourly_rate >= 0", name="ck_cost_rates_rate_nonneg"),
            sa.CheckConstraint(
                "kind IN ('LABOR','MACHINE')", name="ck_cost_rates_kind",
            ),
            # İKİSİ BİRDEN DOLU OLAMAZ ve tür ile bağ tutarlı olmalı.
            # Aksi hâlde "hangi orana bakılacak" sorusu belirsizleşir.
            sa.CheckConstraint(
                "(machine_id IS NULL OR user_id IS NULL)",
                name="ck_cost_rates_single_target",
            ),
            sa.CheckConstraint(
                "(kind <> 'MACHINE' OR user_id IS NULL)",
                name="ck_cost_rates_machine_has_no_user",
            ),
            sa.CheckConstraint(
                "(kind <> 'LABOR' OR machine_id IS NULL)",
                name="ck_cost_rates_labor_has_no_machine",
            ),
        )
        op.create_index(
            "ix_cost_rates_company_kind", "cost_rates", ["company_id", "kind", "status"],
        )
        # HER TÜRDE FİRMA GENELİ VARSAYILAN TEKTİR. İki tane olsaydı hangisinin
        # uygulanacağı satır sırasına kalırdı — yani sessizce değişebilirdi.
        op.create_index(
            "uq_cost_rates_company_default", "cost_rates", ["company_id", "kind"],
            unique=True,
            sqlite_where=sa.text(
                "machine_id IS NULL AND user_id IS NULL AND status = 'ACTIVE'"
            ),
            postgresql_where=sa.text(
                "machine_id IS NULL AND user_id IS NULL AND status = 'ACTIVE'"
            ),
        )
        # Aynı makineye/kullanıcıya iki AKTİF oran da olamaz; aynı gerekçe.
        op.create_index(
            "uq_cost_rates_company_machine", "cost_rates", ["company_id", "machine_id"],
            unique=True,
            sqlite_where=sa.text("machine_id IS NOT NULL AND status = 'ACTIVE'"),
            postgresql_where=sa.text("machine_id IS NOT NULL AND status = 'ACTIVE'"),
        )
        op.create_index(
            "uq_cost_rates_company_user", "cost_rates", ["company_id", "user_id"],
            unique=True,
            sqlite_where=sa.text("user_id IS NOT NULL AND status = 'ACTIVE'"),
            postgresql_where=sa.text("user_id IS NOT NULL AND status = 'ACTIVE'"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    mevcut = set(sa.inspect(bind).get_table_names())
    if "cost_rates" in mevcut:
        op.drop_table("cost_rates")
