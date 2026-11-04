"""Add staff profiles, qualifications, and contract records.

Revision ID: 0012_staff_records
Revises: 0011_timetable
"""
from alembic import op
import sqlalchemy as sa


revision = "0012_staff_records"
down_revision = "0011_timetable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "staff_profiles" not in existing_tables:
        op.create_table(
            "staff_profiles",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("linked_user_id", sa.String(length=32)),
            sa.Column("staff_number", sa.String(length=48), nullable=False),
            sa.Column("first_name", sa.String(length=80), nullable=False),
            sa.Column("last_name", sa.String(length=80), nullable=False),
            sa.Column("email", sa.String(length=254)),
            sa.Column("phone", sa.String(length=32)),
            sa.Column("job_title", sa.String(length=100), nullable=False),
            sa.Column("department", sa.String(length=100)),
            sa.Column("hire_date", sa.Date()),
            sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
            sa.Column("notes", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["linked_user_id"], ["users.id"]),
            sa.UniqueConstraint("staff_number", name="uq_staff_profiles_staff_number"),
            sa.UniqueConstraint("linked_user_id", name="uq_staff_profiles_linked_user"),
            sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_staff_profiles_status"),
        )

    if "staff_qualifications" not in existing_tables:
        op.create_table(
            "staff_qualifications",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("staff_profile_id", sa.String(length=32), nullable=False),
            sa.Column("title", sa.String(length=160), nullable=False),
            sa.Column("institution", sa.String(length=160), nullable=False),
            sa.Column("field_of_study", sa.String(length=120)),
            sa.Column("awarded_on", sa.Date()),
            sa.Column("notes", sa.String(length=500)),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["staff_profile_id"], ["staff_profiles.id"]),
        )
        op.create_index(
            "ix_staff_qualifications_profile_id",
            "staff_qualifications",
            ["staff_profile_id"],
        )

    if "staff_contracts" not in existing_tables:
        op.create_table(
            "staff_contracts",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("staff_profile_id", sa.String(length=32), nullable=False),
            sa.Column("contract_type", sa.String(length=80), nullable=False),
            sa.Column("start_date", sa.Date(), nullable=False),
            sa.Column("end_date", sa.Date()),
            sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
            sa.Column("reference", sa.String(length=80)),
            sa.Column("notes", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["staff_profile_id"], ["staff_profiles.id"]),
            sa.CheckConstraint(
                "status IN ('draft', 'active', 'ended')",
                name="ck_staff_contracts_status",
            ),
            sa.CheckConstraint(
                "end_date IS NULL OR end_date >= start_date",
                name="ck_staff_contracts_date_range",
            ),
        )
        op.create_index(
            "ix_staff_contracts_profile_id",
            "staff_contracts",
            ["staff_profile_id"],
        )


def downgrade() -> None:
    op.drop_index("ix_staff_contracts_profile_id", table_name="staff_contracts")
    op.drop_table("staff_contracts")
    op.drop_index("ix_staff_qualifications_profile_id", table_name="staff_qualifications")
    op.drop_table("staff_qualifications")
    op.drop_table("staff_profiles")
