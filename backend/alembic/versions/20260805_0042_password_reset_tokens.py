"""Şifremi unuttum akışı için tek kullanımlık sıfırlama tokenları.

Revision ID: 20260805_0042
Revises: 20260730_0041

``email_verification_tokens`` ile aynı şekle sahip AYRI bir tablo. Tek tabloya
``purpose`` sütunu eklemek daha az kod olurdu ama bir doğrulama tokenının şifre
sıfırlamaya yarayabilmesi riskini doğururdu: e-posta doğrulama linki kayıt
sırasında üretilir, sızması hesabın ele geçirilmesi anlamına gelmemelidir.
Tokenlar ayrı tabloda durunca bu karışıklık şema seviyesinde imkânsızdır.
"""

from alembic import op
import sqlalchemy as sa

revision = "20260805_0042"
down_revision = "20260730_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("password_reset_tokens"):
        op.create_table(
            "password_reset_tokens",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("app_users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("token_hash", name="uq_password_reset_token_hash"),
        )
    token_indexes = {
        index["name"] for index in sa.inspect(bind).get_indexes("password_reset_tokens")
    }
    if "ix_password_reset_tokens_user_id" not in token_indexes:
        op.create_index(
            "ix_password_reset_tokens_user_id",
            "password_reset_tokens",
            ["user_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("password_reset_tokens"):
        return
    token_indexes = {
        index["name"] for index in inspector.get_indexes("password_reset_tokens")
    }
    if "ix_password_reset_tokens_user_id" in token_indexes:
        op.drop_index(
            "ix_password_reset_tokens_user_id", table_name="password_reset_tokens"
        )
    op.drop_table("password_reset_tokens")
