"""Harman V2c: allocate payments to late-fee charge documents.

Expand-only. ``payment_allocations`` gains a second, mutually exclusive target:
``receivable_charge_id``. ``order_id`` becomes nullable and the XOR is enforced
by a CHECK constraint. PostgreSQL adds the constraint ``NOT VALID`` first, then
validates it explicitly so an existing table is never rewritten under an
exclusive lock for longer than the catalog update.

Revision ID: 20260728_0032
Revises: 20260728_0031
"""

from alembic import op
import sqlalchemy as sa


revision = "20260728_0032"
down_revision = "20260728_0031"
branch_labels = None
depends_on = None


XOR_TARGET_CHECK = (
    "(order_id IS NOT NULL) <> (receivable_charge_id IS NOT NULL)"
)


def _payment_allocations_table(*, with_charge: bool = False) -> sa.Table:
    """The ``payment_allocations`` shape before (or after) this revision.

    Passed to ``batch_alter_table(copy_from=...)`` so the SQLite table rebuild
    never depends on index/constraint reflection.
    """

    metadata = sa.MetaData()
    charge_columns = (
        [sa.Column("receivable_charge_id", sa.Integer())] if with_charge else []
    )
    charge_constraints = (
        [
            sa.CheckConstraint(
                XOR_TARGET_CHECK, name="ck_payment_allocation_target_xor"
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "receivable_charge_id"],
                [
                    "receivable_charge_documents.company_id",
                    "receivable_charge_documents.id",
                ],
                name="fk_payment_allocation_receivable_charge",
                ondelete="RESTRICT",
            ),
        ]
        if with_charge
        else []
    )
    return sa.Table(
        "payment_allocations",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=with_charge),
        *charge_columns,
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("allocation_type", sa.String(length=30), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by", sa.Integer()),
        sa.Column("reversed_at", sa.DateTime(timezone=True)),
        sa.Column("reversed_by", sa.Integer()),
        sa.Column("reversal_allocation_id", sa.Integer()),
        sa.Column("reversal_of_allocation_id", sa.Integer()),
        sa.Column("note", sa.Text()),
        sa.CheckConstraint("amount > 0", name="ck_payment_allocation_amount_positive"),
        sa.CheckConstraint(
            "allocation_type IN ('manual','fifo','document_create','provider')",
            name="ck_payment_allocation_type",
        ),
        sa.CheckConstraint(
            "reversal_allocation_id IS NULL OR reversal_allocation_id <> id",
            name="ck_payment_allocation_not_self_reversed",
        ),
        sa.CheckConstraint(
            "reversal_of_allocation_id IS NULL OR reversal_of_allocation_id <> id",
            name="ck_payment_allocation_not_self_reversal",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["reversal_allocation_id"],
            ["payment_allocations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversal_of_allocation_id"],
            ["payment_allocations.id"],
            ondelete="RESTRICT",
        ),
        sa.Index(
            "ix_payment_allocations_company_payment", "company_id", "payment_id"
        ),
        sa.Index("ix_payment_allocations_company_order", "company_id", "order_id"),
        sa.Index(
            "ix_payment_allocations_company_order_effective",
            "company_id",
            "order_id",
            "effective_date",
        ),
        sa.Index(
            "ix_payment_allocations_company_payment_effective",
            "company_id",
            "payment_id",
            "effective_date",
        ),
        sa.Index(
            "ix_payment_allocations_active_order",
            "company_id",
            "order_id",
            unique=False,
            postgresql_where=sa.text("reversed_at IS NULL"),
            sqlite_where=sa.text("reversed_at IS NULL"),
        ),
        *charge_constraints,
    )


def _ensure_charge_document_company_id_unique(
    bind: sa.engine.Connection,
    inspector: sa.Inspector,
) -> None:
    """The composite FK target must expose ``(company_id, id)`` as unique."""

    constraint_names = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(
            "receivable_charge_documents"
        )
    }
    index_names = {
        index["name"]
        for index in inspector.get_indexes("receivable_charge_documents")
        if index.get("unique")
    }
    if "uq_receivable_charge_document_company_id" in constraint_names | index_names:
        return
    covered = any(
        list(constraint["column_names"]) == ["company_id", "id"]
        for constraint in inspector.get_unique_constraints(
            "receivable_charge_documents"
        )
    )
    if covered:
        return
    op.create_unique_constraint(
        "uq_receivable_charge_document_company_id",
        "receivable_charge_documents",
        ["company_id", "id"],
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name
    if not inspector.has_table("payment_allocations"):
        return
    columns = {column["name"] for column in inspector.get_columns("payment_allocations")}
    if "receivable_charge_id" in columns:
        return

    _ensure_charge_document_company_id_unique(bind, inspector)

    if dialect == "postgresql":
        op.add_column(
            "payment_allocations",
            sa.Column("receivable_charge_id", sa.Integer(), nullable=True),
        )
        op.alter_column("payment_allocations", "order_id", nullable=True)
        op.create_foreign_key(
            "fk_payment_allocation_receivable_charge",
            "payment_allocations",
            "receivable_charge_documents",
            ["company_id", "receivable_charge_id"],
            ["company_id", "id"],
            ondelete="RESTRICT",
        )
        # NOT VALID keeps the ADD CONSTRAINT catalog-only; VALIDATE then scans
        # the existing rows without blocking concurrent writes.
        op.execute(
            "ALTER TABLE payment_allocations "
            "ADD CONSTRAINT ck_payment_allocation_target_xor "
            f"CHECK ({XOR_TARGET_CHECK}) NOT VALID"
        )
        violations = bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM payment_allocations "
                f"WHERE NOT ({XOR_TARGET_CHECK})"
            )
        ).scalar_one()
        if int(violations):
            raise RuntimeError(
                "payment_allocations hedef XOR kuralını ihlal eden "
                f"{int(violations)} satır içeriyor; 0032 doğrulanamadı"
            )
        op.execute(
            "ALTER TABLE payment_allocations "
            "VALIDATE CONSTRAINT ck_payment_allocation_target_xor"
        )
    else:
        with op.batch_alter_table(
            "payment_allocations",
            copy_from=_payment_allocations_table(),
            recreate="always",
        ) as batch:
            batch.add_column(
                sa.Column("receivable_charge_id", sa.Integer(), nullable=True)
            )
            batch.alter_column("order_id", existing_type=sa.Integer(), nullable=True)
            batch.create_foreign_key(
                "fk_payment_allocation_receivable_charge",
                "receivable_charge_documents",
                ["company_id", "receivable_charge_id"],
                ["company_id", "id"],
                ondelete="RESTRICT",
            )
            batch.create_check_constraint(
                "ck_payment_allocation_target_xor",
                sa.text(XOR_TARGET_CHECK),
            )

    op.create_index(
        "ix_payment_allocations_company_charge",
        "payment_allocations",
        ["company_id", "receivable_charge_id"],
    )
    op.create_index(
        "ix_payment_allocations_active_charge",
        "payment_allocations",
        ["company_id", "receivable_charge_id"],
        unique=False,
        postgresql_where=sa.text("reversed_at IS NULL"),
        sqlite_where=sa.text("reversed_at IS NULL"),
    )
    # Separate partial UNIQUE indexes per target. A single index spanning both
    # columns would be silently defeated on PostgreSQL, where NULL targets never
    # collide; splitting them keeps "one active auto allocation per payment and
    # target" enforced for orders and charge documents alike.
    op.create_index(
        "uq_payment_allocations_auto_order",
        "payment_allocations",
        ["company_id", "payment_id", "order_id"],
        unique=True,
        postgresql_where=sa.text(
            "allocation_type='document_create' AND order_id IS NOT NULL "
            "AND reversal_of_allocation_id IS NULL AND reversed_at IS NULL"
        ),
        sqlite_where=sa.text(
            "allocation_type='document_create' AND order_id IS NOT NULL "
            "AND reversal_of_allocation_id IS NULL AND reversed_at IS NULL"
        ),
    )
    op.create_index(
        "uq_payment_allocations_auto_charge",
        "payment_allocations",
        ["company_id", "payment_id", "receivable_charge_id"],
        unique=True,
        postgresql_where=sa.text(
            "allocation_type='document_create' AND receivable_charge_id IS NOT NULL "
            "AND reversal_of_allocation_id IS NULL AND reversed_at IS NULL"
        ),
        sqlite_where=sa.text(
            "allocation_type='document_create' AND receivable_charge_id IS NOT NULL "
            "AND reversal_of_allocation_id IS NULL AND reversed_at IS NULL"
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name
    if not inspector.has_table("payment_allocations"):
        return
    columns = {column["name"] for column in inspector.get_columns("payment_allocations")}
    if "receivable_charge_id" not in columns:
        return
    charge_rows = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM payment_allocations "
            "WHERE receivable_charge_id IS NOT NULL"
        )
    ).scalar_one()
    if int(charge_rows):
        raise RuntimeError(
            "Tahakkuk belgesine tahsis edilmiş satırlar mevcut; 0032 geri alınamaz"
        )
    for index_name in (
        "uq_payment_allocations_auto_charge",
        "uq_payment_allocations_auto_order",
        "ix_payment_allocations_active_charge",
        "ix_payment_allocations_company_charge",
    ):
        op.drop_index(index_name, table_name="payment_allocations")
    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE payment_allocations "
            "DROP CONSTRAINT ck_payment_allocation_target_xor"
        )
        op.drop_constraint(
            "fk_payment_allocation_receivable_charge",
            "payment_allocations",
            type_="foreignkey",
        )
        op.alter_column("payment_allocations", "order_id", nullable=False)
        op.drop_column("payment_allocations", "receivable_charge_id")
    else:
        with op.batch_alter_table(
            "payment_allocations",
            copy_from=_payment_allocations_table(with_charge=True),
            recreate="always",
        ) as batch:
            batch.drop_constraint(
                "ck_payment_allocation_target_xor", type_="check"
            )
            batch.drop_constraint(
                "fk_payment_allocation_receivable_charge", type_="foreignkey"
            )
            batch.alter_column("order_id", existing_type=sa.Integer(), nullable=False)
            batch.drop_column("receivable_charge_id")
