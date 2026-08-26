"""Tarla kurallarını firma bazında ayarlanabilir yapan sütunlar.

Revision ID: 20260807_0048
Revises: 20260807_0047

Owner kararı: alan aşımı, erken hasat ve ilaçlama doz zorunluluğu artık her
firmanın kendi kararı. Kalıp `companies.negative_stock_policy` ile aynı —
politika firma satırında, uygulanması uçlarda.

SEVİYELER VE NEDEN BÖYLE SEÇİLDİLER

``farm_area_override_policy`` — allow | require_reason | block
    Alan aşımı bir ölçüm tutarsızlığı; en gevşek seviyede sessizce geçmesi
    veri kaybı yaratmaz, yalnız dekar başına maliyeti yaklaşık kılar.

``farm_early_harvest_policy`` — warn | require_reason | block
    DİKKAT: EN GEVŞEK SEVİYE "allow" DEĞİL, "warn". Bu bilinçli bir sınır.
    Erken hasat kalıntı riski demek; kontrolü tamamen kapatabilen bir ayar,
    sessiz bir güvenlik kapatma düğmesi olurdu ve o düğmeye bir kez basılıp
    unutulur. "warn" seviyesinde istek KABUL EDİLİYOR ama sistem ne bulduğunu
    ``field_harvests.safety_warning`` sütununa YAZIYOR; yani gevşetme kaydı
    yok etmiyor, yalnız engellemiyor.

``farm_spraying_dose_required`` — boolean
    Doz ve doz birimi zorunluluğu. Kapatan firma dekar başına ilaç kullanımını
    hesaplayamaz; bu bir veri kalitesi tercihidir, güvenlik kapatma değil.

NEDEN AYRI İKİ SÜTUN: ``safety_override_reason`` ve ``safety_warning``.
Birincisi KULLANICININ söylediği, ikincisi SİSTEMİN bulduğu. Aynı sütunda
tutmak, "warn" modunda sistemin uydurduğu bir metni kullanıcının gerekçesi
gibi gösterirdi — denetimde kimin ne dediği ayırt edilemezdi.

Varsayılanlar MEVCUT DAVRANIŞI korur: require_reason / require_reason / true.
Yani bu migration hiçbir firmanın davranışını değiştirmiyor; yalnız değiştirme
imkânı açıyor.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260807_0048"
down_revision = "20260807_0047"
branch_labels = None
depends_on = None

ALAN_SEVIYELERI = ("allow", "require_reason", "block")
HASAT_SEVIYELERI = ("warn", "require_reason", "block")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    firma_sutunlari = {c["name"] for c in inspector.get_columns("companies")}
    if "farm_area_override_policy" not in firma_sutunlari:
        op.add_column(
            "companies",
            sa.Column(
                "farm_area_override_policy",
                sa.String(length=20),
                nullable=False,
                server_default="require_reason",
            ),
        )
    if "farm_early_harvest_policy" not in firma_sutunlari:
        op.add_column(
            "companies",
            sa.Column(
                "farm_early_harvest_policy",
                sa.String(length=20),
                nullable=False,
                server_default="require_reason",
            ),
        )
    if "farm_spraying_dose_required" not in firma_sutunlari:
        op.add_column(
            "companies",
            sa.Column(
                "farm_spraying_dose_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )

    hasat_sutunlari = {c["name"] for c in inspector.get_columns("field_harvests")}
    if "safety_warning" not in hasat_sutunlari:
        # SİSTEMİN bulduğu; kullanıcının gerekçesinden AYRI (bkz. modül başlığı).
        op.add_column("field_harvests", sa.Column("safety_warning", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("field_harvests", "safety_warning")
    op.drop_column("companies", "farm_spraying_dose_required")
    op.drop_column("companies", "farm_early_harvest_policy")
    op.drop_column("companies", "farm_area_override_policy")
