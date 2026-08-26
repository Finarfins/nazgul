"""Add immutable service-fee receivables for completed work orders.

Revision ID: 20260730_0041
Revises: 20260729_0040
"""

from alembic import op
import sqlalchemy as sa


revision = "20260730_0041"
down_revision = "20260729_0040"
branch_labels = None
depends_on = None


SERVICE_SHAPE_CHECK = """
(
  charge_type = 'late_fee'
  AND work_order_id IS NULL
  AND charge_period_id IS NOT NULL
  AND order_id IS NOT NULL
  AND principal_basis IS NOT NULL
  AND annual_rate_snapshot IS NOT NULL
  AND day_count_basis_snapshot IS NOT NULL
  AND grace_days_snapshot IS NOT NULL
  AND tax_mode_snapshot IS NOT NULL
  AND vat_rate_snapshot IS NOT NULL
  AND net_amount IS NOT NULL
  AND vat_amount IS NOT NULL
)
OR
(
  charge_type = 'service_fee'
  AND work_order_id IS NOT NULL
  AND charge_period_id IS NULL
  AND order_id IS NULL
  AND principal_basis IS NULL
  AND annual_rate_snapshot IS NULL
  AND day_count_basis_snapshot IS NULL
  AND grace_days_snapshot IS NULL
  AND tax_mode_snapshot IS NULL
  AND vat_rate_snapshot IS NULL
  AND net_amount IS NULL
  AND vat_amount IS NULL
)
"""

SIGN_CHECK = """
(
  reversal_of_document_id IS NULL
  AND gross_amount >= 0
  AND (
    charge_type <> 'late_fee'
    OR (net_amount >= 0 AND vat_amount >= 0)
  )
)
OR
(
  reversal_of_document_id IS NOT NULL
  AND gross_amount <= 0
  AND (
    charge_type <> 'late_fee'
    OR (net_amount <= 0 AND vat_amount <= 0)
  )
)
"""


NULLABLE_LATE_FEE_COLUMNS = (
    ("charge_period_id", sa.Integer()),
    ("order_id", sa.Integer()),
    ("principal_basis", sa.Numeric(18, 2)),
    ("annual_rate_snapshot", sa.Numeric(9, 4)),
    ("day_count_basis_snapshot", sa.Integer()),
    ("grace_days_snapshot", sa.Integer()),
    ("tax_mode_snapshot", sa.String(length=20)),
    ("vat_rate_snapshot", sa.Numeric(9, 4)),
    ("net_amount", sa.Numeric(18, 2)),
    ("vat_amount", sa.Numeric(18, 2)),
)


def _existing_names(inspector: sa.Inspector, table: str, kind: str) -> set[str]:
    getter = {
        "check": inspector.get_check_constraints,
        "foreign_key": inspector.get_foreign_keys,
        "unique": inspector.get_unique_constraints,
        "index": inspector.get_indexes,
    }[kind]
    return {str(item["name"]) for item in getter(table) if item.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 0029 already creates uq_work_orders_company_row as the unique
    # (company_id, id) target for every work-order composite FK. Creating a
    # second UNIQUE constraint here lets PostgreSQL attach pre-existing FKs to
    # the new constraint, which then makes 0041 impossible to downgrade.
    work_order_indexes = _existing_names(inspector, "work_orders", "index")
    if "uq_work_orders_company_row" not in work_order_indexes:
        raise RuntimeError(
            "0041 requires uq_work_orders_company_row from revision 0029"
        )

    columns = {
        str(column["name"])
        for column in sa.inspect(bind).get_columns("receivable_charge_documents")
    }
    document_inspector = sa.inspect(bind)
    checks = _existing_names(
        document_inspector, "receivable_charge_documents", "check"
    )
    foreign_keys = _existing_names(
        document_inspector, "receivable_charge_documents", "foreign_key"
    )
    with op.batch_alter_table("receivable_charge_documents") as batch:
        if "work_order_id" not in columns:
            batch.add_column(sa.Column("work_order_id", sa.Integer(), nullable=True))
        if "currency" not in columns:
            batch.add_column(
                sa.Column(
                    "currency",
                    sa.String(length=3),
                    nullable=False,
                    server_default="TRY",
                )
            )
        if "exchange_rate" not in columns:
            batch.add_column(
                sa.Column(
                    "exchange_rate",
                    sa.Numeric(18, 6),
                    nullable=False,
                    server_default="1",
                )
            )

        for name, column_type in NULLABLE_LATE_FEE_COLUMNS:
            batch.alter_column(name, existing_type=column_type, nullable=True)

        if "fk_receivable_charge_document_work_order" in foreign_keys:
            batch.drop_constraint(
                "fk_receivable_charge_document_work_order",
                type_="foreignkey",
            )
        for check_name in (
            "ck_receivable_charge_document_source_shape",
            "ck_receivable_charge_document_type",
            "ck_receivable_charge_document_reversal_sign",
            "ck_receivable_charge_document_total",
        ):
            if check_name in checks:
                batch.drop_constraint(check_name, type_="check")
        batch.create_check_constraint(
            "ck_receivable_charge_document_type",
            "charge_type IN ('late_fee','service_fee')",
        )
        batch.create_check_constraint(
            "ck_receivable_charge_document_source_shape",
            sa.text(SERVICE_SHAPE_CHECK),
        )
        batch.create_check_constraint(
            "ck_receivable_charge_document_reversal_sign",
            sa.text(SIGN_CHECK),
        )
        batch.create_check_constraint(
            "ck_receivable_charge_document_total",
            "charge_type <> 'late_fee' OR gross_amount = net_amount + vat_amount",
        )
        batch.create_foreign_key(
            "fk_receivable_charge_document_work_order",
            "work_orders",
            ["company_id", "work_order_id"],
            ["company_id", "id"],
            ondelete="RESTRICT",
        )

    inspector = sa.inspect(bind)
    indexes = _existing_names(inspector, "receivable_charge_documents", "index")
    if "uq_receivable_service_fee_revision" not in indexes:
        op.create_index(
            "uq_receivable_service_fee_revision",
            "receivable_charge_documents",
            ["company_id", "work_order_id", "revision_no"],
            unique=True,
            postgresql_where=sa.text("charge_type='service_fee'"),
            sqlite_where=sa.text("charge_type='service_fee'"),
        )
    if "uq_receivable_service_fee_active" not in indexes:
        active = sa.text(
            "charge_type='service_fee' AND status='posted' "
            "AND reversal_of_document_id IS NULL"
        )
        op.create_index(
            "uq_receivable_service_fee_active",
            "receivable_charge_documents",
            ["company_id", "work_order_id"],
            unique=True,
            postgresql_where=active,
            sqlite_where=active,
        )
    if "ix_receivable_charge_document_type_aging" not in indexes:
        op.create_index(
            "ix_receivable_charge_document_type_aging",
            "receivable_charge_documents",
            ["company_id", "charge_type", "status", "due_date_snapshot"],
        )
    if "ix_receivable_service_fee_history" not in indexes:
        op.create_index(
            "ix_receivable_service_fee_history",
            "receivable_charge_documents",
            ["company_id", "work_order_id", "revision_no"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    service_count = int(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM receivable_charge_documents "
                "WHERE charge_type='service_fee'"
            )
        ).scalar_one()
    )
    if service_count:
        raise RuntimeError(
            "0041 downgrade refused: service_fee receivable rows must be "
            "archived explicitly before removing their schema"
        )

    inspector = sa.inspect(bind)
    indexes = _existing_names(inspector, "receivable_charge_documents", "index")
    for index_name in (
        "ix_receivable_service_fee_history",
        "ix_receivable_charge_document_type_aging",
        "uq_receivable_service_fee_active",
        "uq_receivable_service_fee_revision",
    ):
        if index_name in indexes:
            op.drop_index(index_name, table_name="receivable_charge_documents")

    with op.batch_alter_table("receivable_charge_documents") as batch:
        batch.drop_constraint(
            "fk_receivable_charge_document_work_order", type_="foreignkey"
        )
        batch.drop_constraint(
            "ck_receivable_charge_document_source_shape", type_="check"
        )
        batch.drop_constraint(
            "ck_receivable_charge_document_type", type_="check"
        )
        batch.drop_constraint(
            "ck_receivable_charge_document_reversal_sign", type_="check"
        )
        batch.drop_constraint(
            "ck_receivable_charge_document_total", type_="check"
        )
        batch.create_check_constraint(
            "ck_receivable_charge_document_type", "charge_type = 'late_fee'"
        )
        batch.create_check_constraint(
            "ck_receivable_charge_document_reversal_sign",
            "(reversal_of_document_id IS NULL AND net_amount >= 0 "
            "AND vat_amount >= 0 AND gross_amount >= 0) OR "
            "(reversal_of_document_id IS NOT NULL AND net_amount <= 0 "
            "AND vat_amount <= 0 AND gross_amount <= 0)",
        )
        batch.create_check_constraint(
            "ck_receivable_charge_document_total",
            "gross_amount = net_amount + vat_amount",
        )
        for name, column_type in NULLABLE_LATE_FEE_COLUMNS:
            batch.alter_column(name, existing_type=column_type, nullable=False)
        batch.drop_column("exchange_rate")
        batch.drop_column("currency")
        batch.drop_column("work_order_id")
