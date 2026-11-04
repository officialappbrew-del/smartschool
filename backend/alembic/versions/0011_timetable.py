"""Add weekly timetable entries.

Revision ID: 0011_timetable
Revises: 0010_teacher_parent_messaging
"""
from alembic import op
import sqlalchemy as sa


revision = "0011_timetable"
down_revision = "0010_teacher_parent_messaging"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "timetable_entries" in sa.inspect(op.get_bind()).get_table_names():
        return

    op.create_table(
        "timetable_entries",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("class_section_id", sa.String(length=32), nullable=False),
        sa.Column("teacher_user_id", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=100), nullable=False),
        sa.Column("room", sa.String(length=80)),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.String(length=5), nullable=False),
        sa.Column("end_time", sa.String(length=5), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["class_section_id"], ["class_sections.id"]),
        sa.ForeignKeyConstraint(["teacher_user_id"], ["users.id"]),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_timetable_entries_weekday"),
        sa.CheckConstraint("end_time > start_time", name="ck_timetable_entries_time_range"),
    )
    op.create_index(
        "ix_timetable_entries_weekday_start",
        "timetable_entries",
        ["weekday", "start_time"],
    )
    op.create_index(
        "ix_timetable_entries_class_section_id",
        "timetable_entries",
        ["class_section_id"],
    )
    op.create_index(
        "ix_timetable_entries_teacher_user_id",
        "timetable_entries",
        ["teacher_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_timetable_entries_teacher_user_id", table_name="timetable_entries")
    op.drop_index("ix_timetable_entries_class_section_id", table_name="timetable_entries")
    op.drop_index("ix_timetable_entries_weekday_start", table_name="timetable_entries")
    op.drop_table("timetable_entries")
