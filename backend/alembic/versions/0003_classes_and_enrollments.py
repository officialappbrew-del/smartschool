"""Add academic years, classes, and student enrollment history.

Revision ID: 0003_classes
Revises: 0002_students
"""
from datetime import date
import uuid

from alembic import op
import sqlalchemy as sa


revision = "0003_classes"
down_revision = "0002_students"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = set(sa.inspect(bind).get_table_names())

    if "academic_years" not in existing_tables:
        op.create_table(
            "academic_years",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("name", sa.String(length=32), nullable=False),
            sa.Column("start_date", sa.Date(), nullable=True),
            sa.Column("end_date", sa.Date(), nullable=True),
            sa.Column("is_current", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint("name", name="uq_academic_years_name"),
        )

    if "class_sections" not in existing_tables:
        op.create_table(
            "class_sections",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("name", sa.String(length=80), nullable=False),
            sa.Column("academic_year_id", sa.String(length=32), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(["academic_year_id"], ["academic_years.id"]),
            sa.UniqueConstraint(
                "name",
                "academic_year_id",
                name="uq_class_sections_name_year",
            ),
        )

    if "student_enrollments" not in existing_tables:
        op.create_table(
            "student_enrollments",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("student_id", sa.String(length=32), nullable=False),
            sa.Column("class_section_id", sa.String(length=32), nullable=False),
            sa.Column("enrolled_at", sa.Date(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(["student_id"], ["students.id"]),
            sa.ForeignKeyConstraint(["class_section_id"], ["class_sections.id"]),
            sa.UniqueConstraint(
                "student_id",
                "class_section_id",
                name="uq_enrollments_student_class",
            ),
        )

    year_table = sa.Table(
        "academic_years",
        sa.MetaData(),
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("name", sa.String(length=32)),
        sa.Column("is_current", sa.Boolean()),
    )
    class_table = sa.Table(
        "class_sections",
        sa.MetaData(),
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("name", sa.String(length=80)),
        sa.Column("academic_year_id", sa.String(length=32)),
        sa.Column("is_active", sa.Boolean()),
    )
    student_table = sa.Table(
        "students",
        sa.MetaData(),
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("class_name", sa.String(length=80)),
        sa.Column("academic_year", sa.String(length=32)),
    )
    enrollment_table = sa.Table(
        "student_enrollments",
        sa.MetaData(),
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("student_id", sa.String(length=32)),
        sa.Column("class_section_id", sa.String(length=32)),
        sa.Column("enrolled_at", sa.Date()),
        sa.Column("status", sa.String(length=24)),
    )

    for student in bind.execute(sa.select(student_table)).mappings():
        year_result = bind.execute(
            sa.select(year_table.c.id).where(
                year_table.c.name == student["academic_year"]
            )
        )
        year_id = year_result.scalar_one_or_none()
        if year_id is None:
            year_id = uuid.uuid4().hex
            bind.execute(
                sa.insert(year_table).values(
                    id=year_id,
                    name=student["academic_year"],
                    is_current=False,
                )
            )

        class_result = bind.execute(
            sa.select(class_table.c.id).where(
                class_table.c.name == student["class_name"],
                class_table.c.academic_year_id == year_id,
            )
        )
        class_id = class_result.scalar_one_or_none()
        if class_id is None:
            class_id = uuid.uuid4().hex
            bind.execute(
                sa.insert(class_table).values(
                    id=class_id,
                    name=student["class_name"],
                    academic_year_id=year_id,
                    is_active=True,
                )
            )

        enrollment_exists = bind.execute(
            sa.select(enrollment_table.c.id).where(
                enrollment_table.c.student_id == student["id"],
                enrollment_table.c.class_section_id == class_id,
            )
        ).scalar_one_or_none()
        if enrollment_exists is None:
            bind.execute(
                sa.insert(enrollment_table).values(
                    id=uuid.uuid4().hex,
                    student_id=student["id"],
                    class_section_id=class_id,
                    enrolled_at=date.today(),
                    status="active",
                )
            )


def downgrade() -> None:
    op.drop_table("student_enrollments")
    op.drop_table("class_sections")
    op.drop_table("academic_years")
