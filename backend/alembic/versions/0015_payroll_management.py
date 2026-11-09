"""Add payroll runs and payroll entries for staff compensation.

Revision ID: 0015_payroll_management
Revises: 0014_transport_management
"""
from alembic import op
import sqlalchemy as sa


revision = "0015_payroll_management"
down_revision = "0014_transport_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "payroll_runs" not in existing_tables:
        op.create_table(
            "payroll_runs",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("label", sa.String(length=120), nullable=False),
            sa.Column("pay_period_start", sa.Date(), nullable=False),
            sa.Column("pay_period_end", sa.Date(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
            sa.Column("total_amount", sa.Numeric(precision=14, scale=2), nullable=False, server_default="0"),
            sa.Column("created_by_user_id", sa.String(length=32)),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint("status IN ('draft', 'approved', 'paid')", name="ck_payroll_runs_status"),
            sa.CheckConstraint("pay_period_end >= pay_period_start", name="ck_payroll_runs_period_range"),
        )
    if "payroll_entries" not in existing_tables:
        op.create_table(
            "payroll_entries",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("run_id", sa.String(length=32), nullable=False),
            sa.Column("staff_profile_id", sa.String(length=32), nullable=False),
            sa.Column("base_salary", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0"),
            sa.Column("allowances", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0"),
            sa.Column("deductions", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0"),
            sa.Column("net_pay", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
            sa.Column("notes", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["payroll_runs.id"]),
            sa.ForeignKeyConstraint(["staff_profile_id"], ["staff_profiles.id"]),
            sa.CheckConstraint("base_salary >= 0", name="ck_payroll_entries_base_salary_nonnegative"),
            sa.CheckConstraint("allowances >= 0", name="ck_payroll_entries_allowances_nonnegative"),
            sa.CheckConstraint("deductions >= 0", name="ck_payroll_entries_deductions_nonnegative"),
            sa.CheckConstraint("net_pay >= 0", name="ck_payroll_entries_net_pay_nonnegative"),
            sa.CheckConstraint("status IN ('draft', 'approved', 'paid')", name="ck_payroll_entries_status"),
            sa.UniqueConstraint("run_id", "staff_profile_id", name="uq_payroll_entries_run_staff"),
        )
        op.create_index("ix_payroll_entries_run_id", "payroll_entries", ["run_id"])
        op.create_index("ix_payroll_entries_staff_profile_id", "payroll_entries", ["staff_profile_id"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("payroll_entries"):
        op.drop_index("ix_payroll_entries_staff_profile_id", table_name="payroll_entries")
        op.drop_index("ix_payroll_entries_run_id", table_name="payroll_entries")
        op.drop_table("payroll_entries")
    if sa.inspect(op.get_bind()).has_table("payroll_runs"):
        op.drop_table("payroll_runs")
