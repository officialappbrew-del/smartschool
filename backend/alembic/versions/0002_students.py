"""Add student admission records.

Revision ID: 0002_students
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa


revision = "0002_students"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "students" in inspector.get_table_names():
        return

    op.create_table(
        "students",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("admission_number", sa.String(length=48), nullable=False),
        sa.Column("first_name", sa.String(length=80), nullable=False),
        sa.Column("last_name", sa.String(length=80), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=False),
        sa.Column("gender", sa.String(length=24), nullable=False),
        sa.Column("class_name", sa.String(length=80), nullable=False),
        sa.Column("academic_year", sa.String(length=32), nullable=False),
        sa.Column("guardian_name", sa.String(length=160), nullable=False),
        sa.Column("guardian_email", sa.String(length=254), nullable=True),
        sa.Column("guardian_phone", sa.String(length=32), nullable=False),
        sa.Column("medical_notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("admission_number", name="uq_students_admission_number"),
    )


def downgrade() -> None:
    op.drop_table("students")
