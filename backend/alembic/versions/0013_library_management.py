"""Add library settings, books, and loan records.

Revision ID: 0013_library_management
Revises: 0012_staff_records
"""
from alembic import op
import sqlalchemy as sa


revision = "0013_library_management"
down_revision = "0012_staff_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "library_settings" not in existing_tables:
        op.create_table(
            "library_settings",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("daily_fine_rate", sa.Numeric(precision=8, scale=2), nullable=False, server_default="0"),
            sa.Column("currency", sa.String(length=3), nullable=False, server_default="NGN"),
            sa.Column("updated_by_user_id", sa.String(length=32)),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint("daily_fine_rate >= 0", name="ck_library_settings_fine_rate_nonnegative"),
            sa.CheckConstraint("currency = upper(currency)", name="ck_library_settings_currency_upper"),
        )

    if "library_books" not in existing_tables:
        op.create_table(
            "library_books",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("title", sa.String(length=180), nullable=False),
            sa.Column("author", sa.String(length=160), nullable=False),
            sa.Column("isbn", sa.String(length=32)),
            sa.Column("category", sa.String(length=80)),
            sa.Column("shelf_location", sa.String(length=120)),
            sa.Column("total_copies", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("available_copies", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="available"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint("total_copies > 0", name="ck_library_books_total_copies_positive"),
            sa.CheckConstraint("available_copies >= 0", name="ck_library_books_available_copies_nonnegative"),
            sa.CheckConstraint("status IN ('available', 'archived')", name="ck_library_books_status"),
        )

    if "library_loans" not in existing_tables:
        op.create_table(
            "library_loans",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("book_id", sa.String(length=32), nullable=False),
            sa.Column("student_id", sa.String(length=32), nullable=False),
            sa.Column("borrower_name", sa.String(length=180), nullable=False),
            sa.Column("issued_by_user_id", sa.String(length=32), nullable=False),
            sa.Column("checked_out_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("due_date", sa.Date(), nullable=False),
            sa.Column("returned_at", sa.DateTime(timezone=True)),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("fine_amount", sa.Numeric(precision=10, scale=2), nullable=False, server_default="0"),
            sa.Column("fine_currency", sa.String(length=3), nullable=False, server_default="NGN"),
            sa.Column("notes", sa.String(length=500)),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["book_id"], ["library_books.id"]),
            sa.ForeignKeyConstraint(["student_id"], ["students.id"]),
            sa.ForeignKeyConstraint(["issued_by_user_id"], ["users.id"]),
            sa.CheckConstraint("status IN ('active', 'returned')", name="ck_library_loans_status"),
            sa.CheckConstraint("fine_amount >= 0", name="ck_library_loans_fine_amount_nonnegative"),
        )
        op.create_index("ix_library_loans_book_id", "library_loans", ["book_id"])
        op.create_index("ix_library_loans_student_id", "library_loans", ["student_id"])
        op.create_index("ix_library_loans_due_date", "library_loans", ["due_date"])


def downgrade() -> None:
    op.drop_index("ix_library_loans_due_date", table_name="library_loans")
    op.drop_index("ix_library_loans_student_id", table_name="library_loans")
    op.drop_index("ix_library_loans_book_id", table_name="library_loans")
    op.drop_table("library_loans")
    op.drop_table("library_books")
    op.drop_table("library_settings")
