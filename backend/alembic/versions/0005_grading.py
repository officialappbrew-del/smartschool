"""Add class assessments and student grades.

Revision ID: 0005_grading
Revises: 0004_attendance
"""
from alembic import op
import sqlalchemy as sa


revision = "0005_grading"
down_revision = "0004_attendance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = set(sa.inspect(bind).get_table_names())

    if "assessments" not in existing_tables:
        op.create_table(
            "assessments",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("class_section_id", sa.String(length=32), nullable=False),
            sa.Column("subject", sa.String(length=100), nullable=False),
            sa.Column("title", sa.String(length=120), nullable=False),
            sa.Column("assessment_date", sa.Date(), nullable=False),
            sa.Column("max_score", sa.Numeric(8, 2), nullable=False),
            sa.Column("created_by_user_id", sa.String(length=32), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(["class_section_id"], ["class_sections.id"]),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
            sa.UniqueConstraint(
                "class_section_id",
                "subject",
                "title",
                "assessment_date",
                name="uq_assessments_class_subject_title_date",
            ),
            sa.CheckConstraint(
                "max_score > 0",
                name="ck_assessments_max_score_positive",
            ),
        )

    if "grade_entries" not in existing_tables:
        op.create_table(
            "grade_entries",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("assessment_id", sa.String(length=32), nullable=False),
            sa.Column("student_id", sa.String(length=32), nullable=False),
            sa.Column("score", sa.Numeric(8, 2), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"]),
            sa.ForeignKeyConstraint(["student_id"], ["students.id"]),
            sa.UniqueConstraint(
                "assessment_id",
                "student_id",
                name="uq_grades_assessment_student",
            ),
            sa.CheckConstraint(
                "score >= 0",
                name="ck_grades_score_nonnegative",
            ),
        )


def downgrade() -> None:
    op.drop_table("grade_entries")
    op.drop_table("assessments")
