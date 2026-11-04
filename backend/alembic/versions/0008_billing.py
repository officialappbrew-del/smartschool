"""Add tenant billing settings, fee structures, invoices, and payments.

Revision ID: 0008_billing
Revises: 0007_academic_terms
"""
from alembic import op
import sqlalchemy as sa


revision = "0008_billing"
down_revision = "0007_academic_terms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = set(sa.inspect(bind).get_table_names())

    if "billing_settings" not in existing_tables:
        op.create_table(
            "billing_settings",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("provider", sa.String(length=16), nullable=False, server_default="manual"),
            sa.Column("currency", sa.String(length=3), nullable=False, server_default="NGN"),
            sa.Column("public_key", sa.String(length=255)),
            sa.Column("credentials_encrypted", sa.Text()),
            sa.Column("updated_by_user_id", sa.String(length=32)),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint("provider IN ('manual', 'paystack', 'stripe')", name="ck_billing_settings_provider"),
            sa.CheckConstraint("currency = upper(currency)", name="ck_billing_settings_currency_upper"),
        )

    if "fee_structures" not in existing_tables:
        op.create_table(
            "fee_structures",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("class_section_id", sa.String(length=32), nullable=False),
            sa.Column("term_id", sa.String(length=32), nullable=False),
            sa.Column("description", sa.String(length=120), nullable=False),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("created_by_user_id", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["class_section_id"], ["class_sections.id"]),
            sa.ForeignKeyConstraint(["term_id"], ["academic_terms.id"]),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
            sa.UniqueConstraint("class_section_id", "term_id", "description", name="uq_fee_structures_class_term_description"),
            sa.CheckConstraint("amount > 0", name="ck_fee_structures_amount_positive"),
        )

    if "invoices" not in existing_tables:
        op.create_table(
            "invoices",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("invoice_number", sa.String(length=48), nullable=False),
            sa.Column("enrollment_id", sa.String(length=32), nullable=False),
            sa.Column("term_id", sa.String(length=32), nullable=False),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("currency", sa.String(length=3), nullable=False),
            sa.Column("line_items", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="issued"),
            sa.Column("issued_at", sa.Date(), nullable=False),
            sa.Column("due_date", sa.Date(), nullable=False),
            sa.Column("voided_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["enrollment_id"], ["student_enrollments.id"]),
            sa.ForeignKeyConstraint(["term_id"], ["academic_terms.id"]),
            sa.UniqueConstraint("invoice_number", name="uq_invoices_invoice_number"),
            sa.UniqueConstraint("enrollment_id", "term_id", name="uq_invoices_enrollment_term"),
            sa.CheckConstraint("amount > 0", name="ck_invoices_amount_positive"),
            sa.CheckConstraint("status IN ('issued', 'paid', 'overdue')", name="ck_invoices_status"),
        )

    if "payments" not in existing_tables:
        op.create_table(
            "payments",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("invoice_id", sa.String(length=32), nullable=False),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("method", sa.String(length=24), nullable=False),
            sa.Column("reference", sa.String(length=128)),
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("recorded_by_user_id", sa.String(length=32)),
            sa.Column("voided_at", sa.DateTime(timezone=True)),
            sa.Column("void_reason", sa.String(length=300)),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
            sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.id"]),
            sa.UniqueConstraint("reference", name="uq_payments_reference"),
            sa.CheckConstraint("amount > 0", name="ck_payments_amount_positive"),
            sa.CheckConstraint("method IN ('cash', 'bank_transfer', 'paystack', 'stripe')", name="ck_payments_method"),
        )
        op.create_index("ix_payments_invoice_id", "payments", ["invoice_id"])


def downgrade() -> None:
    op.drop_index("ix_payments_invoice_id", table_name="payments")
    op.drop_table("payments")
    op.drop_table("invoices")
    op.drop_table("fee_structures")
    op.drop_table("billing_settings")
