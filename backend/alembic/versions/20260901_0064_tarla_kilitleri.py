"""Tarla yazma kilidi: ÇKS tek ürün ve tarlaya giriş yasağı.

Revision ID: 20260901_0064
Revises: 20260901_0063

İki kilit BİLEREK aynı göçte. İkisi de `farm.py` yazma yoluna girer; ayrı
PR'lar `tenant_scoping_guard` parmak izinde çarpışırdı.

--- NEDEN BU SÜTUNLAR -------------------------------------------------------

``farm_monoculture_policy`` — warn | require_reason | block
    ÇKS tek ürün kuralı uyum işidir, ölçüm tutarsızlığı değil. Bu yüzden
    ``farm_area_override_policy``deki ``allow`` YOK; en gevşek seviye ``warn``.
    Aynısı ``farm_early_harvest_policy``: kalıntı / uyum kontrolünü tamamen
    kapatabilen bir ayar, sessiz bir güvenlik kapatma düğmesi olurdu.

``farm_reentry_policy`` — warn | require_reason | block
    ``reentry_interval_days`` V1'de toplanıyordu ama HİÇ KULLANILMIYORDU
    (farm.py:1199-1201). PHI kilidi hasat tarafına indi; giriş yasağı yazma
    yolunda hâlâ yoktu. Asimetri tasarım değil. Seviyeler hasat kilidiyle
    aynı: ``allow`` YOK.

``crop_seasons.monoculture_override_reason`` / ``monoculture_warning``
``field_activities.reentry_override_reason`` / ``reentry_warning``
    Hasattaki ``safety_override_reason`` / ``safety_warning`` ayrımının
    ikizi (göç 0048): birincisi KULLANICININ söylediği, ikincisi SİSTEMİN
    bulduğu. Aynı sütunda tutmak, warn modunda sistem metnini kullanıcının
    gerekçesi gibi gösterirdi.

Varsayılanlar require_reason / require_reason: mevcut firmaların davranışı
üçüncü yıl aynı üründe ve yasağı dolmadan faaliyette GEREKÇE İSTER. Göç
satır yazmaz; eski kayıtlarda gerekçe/uyarı NULL = "kilitten önce".
"""
from alembic import op
import sqlalchemy as sa

revision = "20260901_0064"
down_revision = "20260901_0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    firma = {c["name"] for c in inspector.get_columns("companies")}
    if "farm_monoculture_policy" not in firma:
        op.add_column(
            "companies",
            sa.Column(
                "farm_monoculture_policy",
                sa.String(length=20),
                nullable=False,
                server_default="require_reason",
            ),
        )
    if "farm_reentry_policy" not in firma:
        op.add_column(
            "companies",
            sa.Column(
                "farm_reentry_policy",
                sa.String(length=20),
                nullable=False,
                server_default="require_reason",
            ),
        )

    sezon = {c["name"] for c in inspector.get_columns("crop_seasons")}
    if "monoculture_override_reason" not in sezon:
        op.add_column(
            "crop_seasons",
            sa.Column("monoculture_override_reason", sa.String(length=255), nullable=True),
        )
    if "monoculture_warning" not in sezon:
        op.add_column("crop_seasons", sa.Column("monoculture_warning", sa.Text(), nullable=True))

    faaliyet = {c["name"] for c in inspector.get_columns("field_activities")}
    if "reentry_override_reason" not in faaliyet:
        op.add_column(
            "field_activities",
            sa.Column("reentry_override_reason", sa.String(length=255), nullable=True),
        )
    if "reentry_warning" not in faaliyet:
        op.add_column(
            "field_activities", sa.Column("reentry_warning", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    op.drop_column("field_activities", "reentry_warning")
    op.drop_column("field_activities", "reentry_override_reason")
    op.drop_column("crop_seasons", "monoculture_warning")
    op.drop_column("crop_seasons", "monoculture_override_reason")
    op.drop_column("companies", "farm_reentry_policy")
    op.drop_column("companies", "farm_monoculture_policy")
