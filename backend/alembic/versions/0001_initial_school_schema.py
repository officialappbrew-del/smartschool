"""Create baseline school tables and one-time action tokens.

Revision ID: 0001_initial
Revises:
"""
from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())

    if "school_profile" not in existing_tables:
        op.create_table(
            "school_profile",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
        )

    if "users" not in existing_tables:
        op.create_table(
            "users",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("email", sa.String(length=254), nullable=False),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("role", sa.String(length=32), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint("email", name="uq_users_email"),
        )

    if "user_action_tokens" not in existing_tables:
        op.create_table(
            "user_action_tokens",
            sa.Column("token_hash", sa.String(length=64), primary_key=True),
            sa.Column("user_id", sa.String(length=32), nullable=False),
            sa.Column("purpose", sa.String(length=32), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        )


def downgrade() -> None:
    op.drop_table("user_action_tokens")
    op.drop_table("users")
    op.drop_table("school_profile")
