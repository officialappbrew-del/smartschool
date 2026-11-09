"""Add an admission lifecycle status to student records.

Revision ID: 0016_student_admission_tracking
Revises: 0015_payroll_management
"""
from alembic import op
import sqlalchemy as sa


revision = "0016_student_admission_tracking"
down_revision = "0015_payroll_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("students")}
    constraints = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("students")
    }

    with op.batch_alter_table("students") as batch_op:
        if "admission_status" not in columns:
            batch_op.add_column(
                sa.Column(
                    "admission_status",
                    sa.String(length=24),
                    nullable=False,
                    server_default="new",
                ),
            )
        if "ck_students_admission_status" not in constraints:
            batch_op.create_check_constraint(
                "ck_students_admission_status",
                "admission_status IN ('new', 'in_review', 'accepted', 'enrolled', 'waitlist', 'withdrawn')",
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("students")}
    constraints = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("students")
    }

    with op.batch_alter_table("students") as batch_op:
        if "ck_students_admission_status" in constraints:
            batch_op.drop_constraint("ck_students_admission_status", type_="check")
        if "admission_status" in columns:
            batch_op.drop_column("admission_status")
