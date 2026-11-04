"""Add academic terms and link assessments to terms.

Revision ID: 0007_academic_terms
Revises: 0006_teacher_assignments
"""
from alembic import op
import sqlalchemy as sa


revision = "0007_academic_terms"
down_revision = "0006_teacher_assignments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "academic_terms" not in existing_tables:
        op.create_table(
            "academic_terms",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("academic_year_id", sa.String(length=32), nullable=False),
            sa.Column("name", sa.String(length=80), nullable=False),
            sa.Column("start_date", sa.Date(), nullable=False),
            sa.Column("end_date", sa.Date(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["academic_year_id"], ["academic_years.id"]),
            sa.UniqueConstraint(
                "academic_year_id",
                "name",
                name="uq_academic_terms_year_name",
            ),
            sa.CheckConstraint(
                "end_date >= start_date",
                name="ck_academic_terms_date_range",
            ),
        )

    if "assessments" in existing_tables:
        assessment_columns = {
            column["name"] for column in inspector.get_columns("assessments")
        }
        if "academic_term_id" not in assessment_columns:
            with op.batch_alter_table("assessments") as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "academic_term_id",
                        sa.String(length=32),
                        sa.ForeignKey(
                            "academic_terms.id",
                            name="fk_assessments_academic_term_id_academic_terms",
                        ),
                        nullable=True,
                    )
                )


def downgrade() -> None:
    if "academic_term_id" in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("assessments")
    }:
        with op.batch_alter_table("assessments") as batch_op:
            batch_op.drop_column("academic_term_id")
    op.drop_table("academic_terms")
