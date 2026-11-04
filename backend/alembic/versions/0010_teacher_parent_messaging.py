"""Add threaded teacher-parent messaging and moderation flags.

Revision ID: 0010_teacher_parent_messaging
Revises: 0009_announcements
"""
from alembic import op
import sqlalchemy as sa


revision = "0010_teacher_parent_messaging"
down_revision = "0009_announcements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "message_threads" not in existing_tables:
        op.create_table(
            "message_threads",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("student_id", sa.String(length=32), nullable=False),
            sa.Column("teacher_user_id", sa.String(length=32), nullable=False),
            sa.Column("guardian_user_id", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["student_id"], ["students.id"]),
            sa.ForeignKeyConstraint(["teacher_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["guardian_user_id"], ["users.id"]),
            sa.UniqueConstraint(
                "student_id",
                "teacher_user_id",
                "guardian_user_id",
                name="uq_message_threads_participants",
            ),
        )

    if "thread_messages" not in existing_tables:
        op.create_table(
            "thread_messages",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("thread_id", sa.String(length=32), nullable=False),
            sa.Column("sender_user_id", sa.String(length=32), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["thread_id"], ["message_threads.id"]),
            sa.ForeignKeyConstraint(["sender_user_id"], ["users.id"]),
        )
        op.create_index("ix_thread_messages_thread_id", "thread_messages", ["thread_id"])

    if "message_flags" not in existing_tables:
        op.create_table(
            "message_flags",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("message_id", sa.String(length=32), nullable=False),
            sa.Column("reported_by_user_id", sa.String(length=32), nullable=False),
            sa.Column("reason", sa.String(length=500), nullable=False),
            sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("reviewed_by_user_id", sa.String(length=32)),
            sa.Column("reviewed_at", sa.DateTime(timezone=True)),
            sa.ForeignKeyConstraint(["message_id"], ["thread_messages.id"]),
            sa.ForeignKeyConstraint(["reported_by_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"]),
            sa.UniqueConstraint("message_id", name="uq_message_flags_message"),
            sa.CheckConstraint(
                "status IN ('open', 'reviewed', 'dismissed')",
                name="ck_message_flags_status",
            ),
        )
        op.create_index("ix_message_flags_status", "message_flags", ["status"])


def downgrade() -> None:
    op.drop_index("ix_message_flags_status", table_name="message_flags")
    op.drop_table("message_flags")
    op.drop_index("ix_thread_messages_thread_id", table_name="thread_messages")
    op.drop_table("thread_messages")
    op.drop_table("message_threads")
