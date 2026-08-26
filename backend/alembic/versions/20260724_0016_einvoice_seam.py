"""e-Fatura seam: pluggable e-invoice columns on ``invoices`` (default NoOp).

Adds nullable / defaulted ``einvoice_*`` columns so the internal invoice flow is
unchanged and existing rows are untouched, while the UBL/JSON payload can be
built and persisted now. The real İzibiz/Nes adapter later is a one-file + config
drop-in — no schema change required then.

``einvoice_payload`` is TEXT holding a JSON string (NOT JSONB): the codebase keeps
SQLite<->PostgreSQL16 parity and has been bitten by dialect JSON typing before.

Revision ID: 20260724_0016
Revises: 20260723_0015
"""

from alembic import op
import sqlalchemy as sa


revision = "20260724_0016"
down_revision = "20260723_0015"
branch_labels = None
depends_on = None


# (name, column factory) — all nullable or defaulted, so ADD COLUMN is safe on
# existing rows on both SQLite and PostgreSQL.
_COLUMNS = (
    ("einvoice_channel", lambda: sa.Column("einvoice_channel", sa.String(length=10))),
    ("einvoice_status", lambda: sa.Column("einvoice_status", sa.String(length=20), nullable=False, server_default="NONE")),
    ("einvoice_uuid", lambda: sa.Column("einvoice_uuid", sa.String(length=36))),
    ("einvoice_external_id", lambda: sa.Column("einvoice_external_id", sa.String(length=64))),
    ("einvoice_last_error", lambda: sa.Column("einvoice_last_error", sa.Text())),
    ("einvoice_payload", lambda: sa.Column("einvoice_payload", sa.Text())),
    ("einvoice_submitted_at", lambda: sa.Column("einvoice_submitted_at", sa.DateTime(timezone=True))),
    ("einvoice_updated_at", lambda: sa.Column("einvoice_updated_at", sa.DateTime(timezone=True))),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("invoices")}
    for name, make_column in _COLUMNS:
        if name not in existing:
            op.add_column("invoices", make_column())
    indexes = {ix["name"] for ix in inspector.get_indexes("invoices")}
    if "ix_invoices_einvoice_status" not in indexes:
        op.create_index(
            "ix_invoices_einvoice_status", "invoices", ["company_id", "einvoice_status"]
        )


def downgrade() -> None:
    op.drop_index("ix_invoices_einvoice_status", table_name="invoices")
    for name, _ in reversed(_COLUMNS):
        op.drop_column("invoices", name)
