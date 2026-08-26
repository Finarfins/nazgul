"""Create immutable enterprise invoice aggregate.

Revision ID: 20260718_0012
Revises: 20260717_0011
"""
from alembic import op
import sqlalchemy as sa

revision="20260718_0012"; down_revision="20260717_0011"; branch_labels=None; depends_on=None

def upgrade() -> None:
    op.create_table("invoice_counters",
        sa.Column("company_id",sa.Integer(),nullable=False),sa.Column("year",sa.Integer(),nullable=False),
        sa.Column("branch_prefix",sa.String(20),nullable=False,server_default=""),sa.Column("current_value",sa.Integer(),nullable=False,server_default="0"),
        sa.PrimaryKeyConstraint("company_id","year","branch_prefix"),sa.ForeignKeyConstraint(["company_id"],["companies.id"]))
    op.create_table("invoices",
        sa.Column("id",sa.Integer(),primary_key=True,autoincrement=True),sa.Column("company_id",sa.Integer(),nullable=False),
        sa.Column("work_order_id",sa.Integer()),sa.Column("invoice_number",sa.String(80),nullable=False),
        sa.Column("invoice_type",sa.String(20),nullable=False,server_default="INVOICE"),sa.Column("status",sa.String(20),nullable=False,server_default="ISSUED"),
        sa.Column("currency",sa.String(3),nullable=False,server_default="TRY"),sa.Column("exchange_rate",sa.Numeric(18,6),nullable=False,server_default="1"),
        sa.Column("customer_snapshot",sa.Text(),nullable=False),sa.Column("machine_snapshot",sa.Text(),nullable=False),
        sa.Column("work_order_snapshot",sa.Text(),nullable=False),sa.Column("company_snapshot",sa.Text(),nullable=False),
        sa.Column("technician_snapshot",sa.Text(),nullable=False),sa.Column("warranty_snapshot",sa.Text(),nullable=False),
        sa.Column("totals_snapshot",sa.Text(),nullable=False),sa.Column("tax_snapshot",sa.Text(),nullable=False),
        sa.Column("payment_terms",sa.Text()),sa.Column("notes",sa.Text()),sa.Column("created_by",sa.Integer(),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("cancelled_at",sa.DateTime(timezone=True)),sa.Column("cancel_reason",sa.Text()),
        sa.UniqueConstraint("company_id","invoice_number",name="uq_invoices_company_number"),
        sa.UniqueConstraint("company_id","work_order_id","invoice_type",name="uq_invoices_work_order_type"),
        sa.ForeignKeyConstraint(["company_id"],["companies.id"]),sa.ForeignKeyConstraint(["work_order_id"],["work_orders.id"]),sa.ForeignKeyConstraint(["created_by"],["app_users.id"]))
    op.create_index("ix_invoices_company_status_date","invoices",["company_id","status","created_at"])
    op.create_index("ix_invoices_company_work_order","invoices",["company_id","work_order_id"])
    op.create_table("invoice_items",
        sa.Column("id",sa.Integer(),primary_key=True,autoincrement=True),sa.Column("company_id",sa.Integer(),nullable=False),sa.Column("invoice_id",sa.Integer(),nullable=False),
        sa.Column("item_type",sa.String(20),nullable=False),sa.Column("description",sa.String(500),nullable=False),
        sa.Column("quantity",sa.Numeric(18,4),nullable=False),sa.Column("unit_price",sa.Numeric(18,2),nullable=False),
        sa.Column("original_price",sa.Numeric(18,2),nullable=False),sa.Column("discount_type",sa.String(20),nullable=False,server_default="PERCENT"),
        sa.Column("discount_value",sa.Numeric(18,4),nullable=False,server_default="0"),sa.Column("discount_amount",sa.Numeric(18,2),nullable=False),
        sa.Column("tax_rate",sa.Numeric(9,4),nullable=False),sa.Column("tax_exempt",sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column("tax_amount",sa.Numeric(18,2),nullable=False),sa.Column("total",sa.Numeric(18,2),nullable=False),
        sa.Column("warranty_percent",sa.Numeric(9,4),nullable=False),sa.Column("customer_payable",sa.Numeric(18,2),nullable=False),
        sa.Column("company_payable",sa.Numeric(18,2),nullable=False),sa.Column("source_snapshot",sa.Text(),nullable=False),
        sa.ForeignKeyConstraint(["company_id"],["companies.id"]),sa.ForeignKeyConstraint(["invoice_id"],["invoices.id"],ondelete="CASCADE"))
    op.create_index("ix_invoice_items_invoice","invoice_items",["company_id","invoice_id","id"])
    op.create_table("invoice_history",
        sa.Column("id",sa.Integer(),primary_key=True,autoincrement=True),sa.Column("company_id",sa.Integer(),nullable=False),sa.Column("invoice_id",sa.Integer(),nullable=False),
        sa.Column("action",sa.String(30),nullable=False),sa.Column("actor_user_id",sa.Integer()),sa.Column("actor_username",sa.String(160)),
        sa.Column("ip_address",sa.String(64)),sa.Column("reason",sa.Text()),sa.Column("metadata_json",sa.Text()),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(["company_id"],["companies.id"]),sa.ForeignKeyConstraint(["invoice_id"],["invoices.id"],ondelete="CASCADE"))
    op.create_index("ix_invoice_history_lookup","invoice_history",["company_id","invoice_id","id"])
    op.create_table("invoice_audit",
        sa.Column("id",sa.Integer(),primary_key=True,autoincrement=True),sa.Column("company_id",sa.Integer(),nullable=False),sa.Column("invoice_id",sa.Integer(),nullable=False),
        sa.Column("action",sa.String(30),nullable=False),sa.Column("actor_user_id",sa.Integer()),sa.Column("actor_username",sa.String(160)),
        sa.Column("ip_address",sa.String(64)),sa.Column("reason",sa.Text()),sa.Column("metadata_json",sa.Text()),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(["company_id"],["companies.id"]),sa.ForeignKeyConstraint(["invoice_id"],["invoices.id"],ondelete="CASCADE"))
    op.create_index("ix_invoice_audit_lookup","invoice_audit",["company_id","invoice_id","id"])

def downgrade() -> None:
    op.drop_table("invoice_audit"); op.drop_table("invoice_history"); op.drop_table("invoice_items"); op.drop_table("invoices"); op.drop_table("invoice_counters")
