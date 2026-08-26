"""Self-service company registration and email verification.

Revision ID: 20260729_0039
Revises: 20260729_0038
"""

from alembic import op
import sqlalchemy as sa

revision = "20260729_0039"
down_revision = "20260729_0038"
branch_labels = None
depends_on = None


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    return (
        {column["name"] for column in inspector.get_columns(table)}
        if inspector.has_table(table)
        else set()
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = _columns(inspector, "app_users")

    if "email" not in user_columns:
        op.add_column("app_users", sa.Column("email", sa.String(320), nullable=True))
    op.execute(
        sa.text(
            "UPDATE app_users SET email = "
            "'legacy-user-' || CAST(id AS VARCHAR(40)) || '@legacy.invalid' "
            "WHERE email IS NULL"
        )
    )
    inspector = sa.inspect(bind)
    unique_columns = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("app_users")
    }
    with op.batch_alter_table("app_users") as batch:
        batch.alter_column("email", existing_type=sa.String(320), nullable=False)
        if ("email",) not in unique_columns:
            batch.create_unique_constraint("uq_app_users_email", ["email"])

    inspector = sa.inspect(bind)
    user_columns = _columns(inspector, "app_users")
    if "email_verified" not in user_columns:
        op.add_column(
            "app_users",
            sa.Column("email_verified", sa.Boolean(), nullable=True),
        )
    op.execute(sa.text("UPDATE app_users SET email_verified = true"))
    with op.batch_alter_table("app_users") as batch:
        batch.alter_column(
            "email_verified",
            existing_type=sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        )

    company_columns = _columns(sa.inspect(bind), "companies")
    if "tax_number" not in company_columns:
        op.add_column("companies", sa.Column("tax_number", sa.String(40), nullable=True))

    if not sa.inspect(bind).has_table("email_verification_tokens"):
        op.create_table(
            "email_verification_tokens",
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
            sa.UniqueConstraint("token_hash", name="uq_email_verification_token_hash"),
        )
    token_indexes = {
        index["name"]
        for index in sa.inspect(bind).get_indexes("email_verification_tokens")
    }
    if "ix_email_verification_tokens_user_id" not in token_indexes:
        op.create_index(
            "ix_email_verification_tokens_user_id",
            "email_verification_tokens",
            ["user_id"],
        )

    if not sa.inspect(bind).has_table("auth_rate_limits"):
        op.create_table(
            "auth_rate_limits",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("action", sa.String(40), nullable=False),
            sa.Column("ip_address", sa.String(80), nullable=False),
            sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        )
    rate_indexes = {
        index["name"] for index in sa.inspect(bind).get_indexes("auth_rate_limits")
    }
    if "ix_auth_rate_limits_action_ip_time" not in rate_indexes:
        op.create_index(
            "ix_auth_rate_limits_action_ip_time",
            "auth_rate_limits",
            ["action", "ip_address", "attempted_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("auth_rate_limits"):
        rate_indexes = {
            index["name"] for index in inspector.get_indexes("auth_rate_limits")
        }
        if "ix_auth_rate_limits_action_ip_time" in rate_indexes:
            op.drop_index(
                "ix_auth_rate_limits_action_ip_time", table_name="auth_rate_limits"
            )
        op.drop_table("auth_rate_limits")
        inspector = sa.inspect(bind)
    if inspector.has_table("email_verification_tokens"):
        token_indexes = {
            index["name"]
            for index in inspector.get_indexes("email_verification_tokens")
        }
        if "ix_email_verification_tokens_user_id" in token_indexes:
            op.drop_index(
                "ix_email_verification_tokens_user_id",
                table_name="email_verification_tokens",
            )
        op.drop_table("email_verification_tokens")

    inspector = sa.inspect(bind)
    user_columns = _columns(inspector, "app_users")
    unique_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("app_users")
        if constraint.get("name")
    }
    with op.batch_alter_table("app_users") as batch:
        if "email_verified" in user_columns:
            batch.drop_column("email_verified")
        if "email" in user_columns:
            if "uq_app_users_email" in unique_constraints:
                batch.drop_constraint("uq_app_users_email", type_="unique")
            batch.drop_column("email")

    company_columns = _columns(sa.inspect(bind), "companies")
    if "tax_number" in company_columns:
        # tax_number predates this migration in normal installations. Only a
        # partial legacy schema would have received it here, and Alembic cannot
        # distinguish that origin during downgrade, so preserve business data.
        pass
