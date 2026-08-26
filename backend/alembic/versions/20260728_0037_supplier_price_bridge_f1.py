"""Supplier price bridge phase 1.

Revision ID: 20260728_0037
Revises: 20260728_0036
"""

from alembic import op
import sqlalchemy as sa

revision = "20260728_0037"
down_revision = "20260728_0036"
branch_labels = None
depends_on = None


def _create_table(inspector: sa.Inspector, name: str, *columns: object) -> None:
    if not inspector.has_table(name):
        op.create_table(name, *columns)


def _repair_indexes(inspector: sa.Inspector) -> None:
    import_indexes = {
        index["name"] for index in inspector.get_indexes("supplier_price_imports")
    }
    if "uq_supplier_price_import_active_hash" not in import_indexes:
        active = sa.text("status IN ('DRY_RUN_READY','APPLIED')")
        op.create_index(
            "uq_supplier_price_import_active_hash",
            "supplier_price_imports",
            ["company_id", "source_sha256", "status"],
            unique=True,
            postgresql_where=active,
            sqlite_where=active,
        )
    line_indexes = {
        index["name"]
        for index in inspector.get_indexes("supplier_price_import_lines")
    }
    if "ix_supplier_price_import_line_state" not in line_indexes:
        op.create_index(
            "ix_supplier_price_import_line_state",
            "supplier_price_import_lines",
            ["import_id", "state"],
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _create_table(
        inspector,
        "supplier_import_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("sheet_selector", sa.String(255), nullable=False),
        sa.Column("header_row_strategy", sa.String(32), nullable=False),
        sa.Column("column_map", sa.JSON(), nullable=False),
        sa.Column("currency_mode", sa.String(32), nullable=False),
        sa.Column("term_days", sa.Integer(), nullable=False),
        sa.Column("vat_source", sa.String(32), nullable=False),
        sa.Column("warning_threshold", sa.Numeric(9, 2), nullable=False, server_default="25"),
        sa.Column("blocked_threshold", sa.Numeric(9, 2), nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Integer()),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("company_id", "supplier_id", "name"),
    )
    _create_table(
        inspector,
        "supplier_price_imports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("source_filename", sa.String(255), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("source_bytes", sa.BigInteger(), nullable=False),
        sa.Column("parsed_row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unmatched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revision", sa.BigInteger()),
        sa.Column("report_digest", sa.String(64)),
        sa.Column("after_digest", sa.String(64)),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("applied_by", sa.Integer()),
        sa.Column("reverted_at", sa.DateTime(timezone=True)),
        sa.Column("reverted_by", sa.Integer()),
        sa.Column("error_detail", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Integer()),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["supplier_import_profiles.id"]),
    )
    _create_table(
        inspector,
        "supplier_price_import_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("import_id", sa.Integer(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.Column("supplier_code", sa.String(255)),
        sa.Column("description", sa.Text()),
        sa.Column("unit", sa.String(64)),
        sa.Column("price_cash", sa.Numeric(18, 4)),
        sa.Column("price_term", sa.Numeric(18, 4)),
        sa.Column("currency", sa.String(3)),
        sa.Column("vat_rate", sa.Numeric(5, 2)),
        sa.Column("part_id", sa.Integer()),
        sa.Column("match_source", sa.String(24)),
        sa.Column("old_price_cash", sa.Numeric(18, 4)),
        sa.Column("old_price_term", sa.Numeric(18, 4)),
        sa.Column("old_currency", sa.String(3)),
        sa.Column("old_vat_rate", sa.Numeric(5, 2)),
        sa.Column("deviation_pct", sa.Numeric(9, 2)),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("override_by", sa.Integer()),
        sa.Column("override_at", sa.DateTime(timezone=True)),
        sa.Column("override_reason", sa.Text()),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["import_id"], ["supplier_price_imports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["part_id"], ["products.id"]),
        sa.UniqueConstraint("import_id", "line_no"),
    )
    _create_table(
        inspector,
        "supplier_part_xrefs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("supplier_code", sa.String(255), nullable=False),
        sa.Column("part_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Integer()),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["part_id"], ["products.id"]),
        sa.UniqueConstraint("company_id", "supplier_id", "supplier_code"),
    )
    _create_table(
        inspector,
        "supplier_part_prices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("part_id", sa.Integer(), nullable=False),
        sa.Column("price_cash", sa.Numeric(18, 4)),
        sa.Column("price_term", sa.Numeric(18, 4)),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("vat_rate", sa.Numeric(5, 2), nullable=False),
        sa.Column("term_days", sa.Integer(), nullable=False),
        sa.Column("source_import_id", sa.Integer(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["part_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["source_import_id"], ["supplier_price_imports.id"]),
        sa.UniqueConstraint("company_id", "supplier_id", "part_id"),
    )
    _repair_indexes(sa.inspect(bind))


def downgrade() -> None:
    op.drop_table("supplier_part_prices")
    op.drop_table("supplier_part_xrefs")
    op.drop_index("ix_supplier_price_import_line_state", table_name="supplier_price_import_lines")
    op.drop_table("supplier_price_import_lines")
    op.drop_index("uq_supplier_price_import_active_hash", table_name="supplier_price_imports")
    op.drop_table("supplier_price_imports")
    op.drop_table("supplier_import_profiles")
