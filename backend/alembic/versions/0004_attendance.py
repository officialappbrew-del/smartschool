"""Add daily attendance records and sync idempotency keys.

Revision ID: 0004_attendance
Revises: 0003_classes
"""
from alembic import op
import sqlalchemy as sa


revision = "0004_attendance"
down_revision = "0003_classes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "attendance_records" in sa.inspect(op.get_bind()).get_table_names():
        return

    op.create_table(
        "attendance_records",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("student_id", sa.String(length=32), nullable=False),
        sa.Column("class_section_id", sa.String(length=32), nullable=False),
        sa.Column("attendance_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("marked_by_user_id", sa.String(length=32), nullable=False),
        sa.Column("marked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("local_id", sa.String(length=36), nullable=False),
        sa.Column("client_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"]),
        sa.ForeignKeyConstraint(["class_section_id"], ["class_sections.id"]),
        sa.ForeignKeyConstraint(["marked_by_user_id"], ["users.id"]),
        sa.UniqueConstraint(
            "student_id",
            "class_section_id",
            "attendance_date",
            name="uq_attendance_student_class_date",
        ),
        sa.UniqueConstraint("local_id", name="uq_attendance_local_id"),
    )


def downgrade() -> None:
    op.drop_table("attendance_records")
