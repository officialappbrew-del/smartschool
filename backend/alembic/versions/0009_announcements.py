"""Add tenant announcements and email delivery tracking.

Revision ID: 0009_announcements
Revises: 0008_billing
"""
from alembic import op
import sqlalchemy as sa


revision = "0009_announcements"
down_revision = "0008_billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "announcements" not in existing_tables:
        op.create_table(
            "announcements",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("title", sa.String(length=160), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
            sa.Column("created_by_user_id", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("published_at", sa.DateTime(timezone=True)),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
            sa.CheckConstraint("status IN ('draft', 'published')", name="ck_announcements_status"),
        )

    if "announcement_deliveries" not in existing_tables:
        op.create_table(
            "announcement_deliveries",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("announcement_id", sa.String(length=32), nullable=False),
            sa.Column("recipient_email", sa.String(length=254), nullable=False),
            sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
            sa.Column("sent_at", sa.DateTime(timezone=True)),
            sa.Column("last_error", sa.String(length=500)),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["announcement_id"], ["announcements.id"]),
            sa.UniqueConstraint(
                "announcement_id",
                "recipient_email",
                name="uq_announcement_delivery_recipient",
            ),
            sa.CheckConstraint(
                "status IN ('pending', 'sent', 'failed')",
                name="ck_announcement_delivery_status",
            ),
        )
        op.create_index(
            "ix_announcement_deliveries_announcement_id",
            "announcement_deliveries",
            ["announcement_id"],
        )


def downgrade() -> None:
    op.drop_index(
        "ix_announcement_deliveries_announcement_id",
        table_name="announcement_deliveries",
    )
    op.drop_table("announcement_deliveries")
    op.drop_table("announcements")
