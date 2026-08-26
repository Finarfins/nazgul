"""V3 Harman Vadeli (harvest-term) finance — Increment 1.

Adds the payment-term dimension to sales so a dealer can defer collection to the
harvest season ("harman vadesi"). Two nullable/defaulted columns on ``orders``:

- ``payment_term`` : PESIN (default, legacy behaviour) | HARMAN_VADELI.
- ``due_date``     : the harvest-season payment date. Already present on the
  baseline ``orders`` table (String), so it is only created here on legacy
  installs that predate it — guarded by an inspector check.

Everything is defaulted and parity-safe (SQLite + PostgreSQL 16): existing rows
and PESIN sales are untouched. Money stays NUMERIC; no float anywhere.

Revision ID: 20260724_0017
Revises: 20260724_0016

NOTE (single-head coordination): this lane chains onto develop's current head,
the e-Fatura seam (0016). Other V3 lanes add migrations in parallel; if develop's
Alembic head advances again before merge, re-point ``down_revision`` to the new
head so the tree keeps a single Alembic head. Cowork coordinates the order.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260724_0017"
down_revision = "20260724_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {column["name"] for column in inspector.get_columns("orders")}

    with op.batch_alter_table("orders") as batch:
        if "payment_term" not in existing:
            batch.add_column(
                sa.Column(
                    "payment_term",
                    sa.String(length=20),
                    nullable=False,
                    server_default="PESIN",
                )
            )
        # due_date is part of the baseline schema; only legacy installs missing it
        # need it created here. Kept nullable — PESIN sales carry no due date.
        if "due_date" not in existing:
            batch.add_column(sa.Column("due_date", sa.String(length=30), nullable=True))

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("orders")}
    if "ix_orders_company_term_due" not in indexes:
        op.create_index(
            "ix_orders_company_term_due",
            "orders",
            ["company_id", "payment_term", "due_date"],
        )


def downgrade() -> None:
    raise RuntimeError("Harman vadeli payment-term downgrade is intentionally disabled")
