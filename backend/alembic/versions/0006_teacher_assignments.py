"""Add teacher class assignments.

Revision ID: 0006_teacher_assignments
Revises: 0005_grading
"""
from alembic import op
import sqlalchemy as sa


revision = "0006_teacher_assignments"
down_revision = "0005_grading"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "teacher_class_assignments" in sa.inspect(op.get_bind()).get_table_names():
        return

    op.create_table(
        "teacher_class_assignments",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("teacher_user_id", sa.String(length=32), nullable=False),
        sa.Column("class_section_id", sa.String(length=32), nullable=False),
        sa.Column("assigned_by_user_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["teacher_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["class_section_id"], ["class_sections.id"]),
        sa.ForeignKeyConstraint(["assigned_by_user_id"], ["users.id"]),
        sa.UniqueConstraint(
            "teacher_user_id",
            "class_section_id",
            name="uq_teacher_class_assignment",
        ),
    )


def downgrade() -> None:
    op.drop_table("teacher_class_assignments")
