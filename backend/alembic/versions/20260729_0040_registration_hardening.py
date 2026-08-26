"""Registration hardening and explicit legacy-email preflight.

Revision ID: 20260729_0040
Revises: 20260729_0039
"""

from alembic import op
import sqlalchemy as sa

revision = "20260729_0040"
down_revision = "20260729_0039"
branch_labels = None
depends_on = None


def _ids(rows: list[tuple]) -> str:
    return ", ".join(str(value) for row in rows for value in row if value is not None)


def _preflight_email_uniqueness(bind: sa.Connection) -> None:
    duplicates = bind.execute(
        sa.text(
            "SELECT id FROM app_users WHERE email IS NOT NULL AND LOWER(email) IN ("
            "SELECT LOWER(email) FROM app_users WHERE email IS NOT NULL "
            "GROUP BY LOWER(email) HAVING COUNT(*) > 1) ORDER BY id"
        )
    ).all()
    if duplicates:
        raise RuntimeError(
            "Migration durduruldu: tekrar eden e-posta bulunan app_users id listesi: "
            f"{_ids(duplicates)}"
        )

    collisions = bind.execute(
        sa.text(
            "SELECT empty_user.id, existing_user.id "
            "FROM app_users AS empty_user "
            "JOIN app_users AS existing_user "
            "ON LOWER(existing_user.email) = "
            "LOWER('legacy-user-' || CAST(empty_user.id AS VARCHAR(40)) || '@legacy.invalid') "
            "WHERE empty_user.email IS NULL"
        )
    ).all()
    if collisions:
        raise RuntimeError(
            "Migration durduruldu: üretilecek legacy e-posta mevcut değerle çakışıyor; "
            f"etkilenen app_users id listesi: {_ids(collisions)}"
        )


def upgrade() -> None:
    bind = op.get_bind()
    _preflight_email_uniqueness(bind)
    bind.execute(
        sa.text(
            "UPDATE app_users SET email = LOWER(TRIM(email)) "
            "WHERE email IS NOT NULL"
        )
    )
    indexes = {
        index["name"] for index in sa.inspect(bind).get_indexes("app_users")
    }
    if "uq_app_users_email_lower" not in indexes:
        op.create_index(
            "uq_app_users_email_lower",
            "app_users",
            [sa.text("LOWER(email)")],
            unique=True,
        )
    columns = {
        column["name"] for column in sa.inspect(bind).get_columns("app_users")
    }
    if "phone" not in columns:
        op.add_column("app_users", sa.Column("phone", sa.String(10), nullable=True))
    if "terms_accepted_at" not in columns:
        op.add_column(
            "app_users",
            sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("app_users")}
    indexes = {index["name"] for index in inspector.get_indexes("app_users")}
    if "uq_app_users_email_lower" in indexes:
        op.drop_index("uq_app_users_email_lower", table_name="app_users")
    with op.batch_alter_table("app_users") as batch:
        if "terms_accepted_at" in columns:
            batch.drop_column("terms_accepted_at")
        if "phone" in columns:
            batch.drop_column("phone")
