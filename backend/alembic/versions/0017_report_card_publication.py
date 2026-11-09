"""Add report-card publication and notification tracking."""

from alembic import op
import sqlalchemy as sa


revision = "0017_report_card_publication"
down_revision = "0016_student_admission_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("report_card_publications"):
        op.create_table(
            "report_card_publications",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("class_section_id", sa.String(length=32), nullable=False),
            sa.Column("term_id", sa.String(length=32), nullable=False),
            sa.Column("published_by_user_id", sa.String(length=32), nullable=False),
            sa.Column(
                "published_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "class_section_id",
                "term_id",
                name="uq_report_card_publications_class_term",
            ),
        )

    if not sa.inspect(op.get_bind()).has_table("report_card_notifications"):
        op.create_table(
            "report_card_notifications",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("publication_id", sa.String(length=32), nullable=False),
            sa.Column("student_id", sa.String(length=32), nullable=False),
            sa.Column("recipient_email", sa.String(length=254), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
            sa.Column("last_error", sa.String(length=500)),
            sa.Column("sent_at", sa.DateTime(timezone=True)),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["publication_id"],
                ["report_card_publications.id"],
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint(
                "publication_id",
                "student_id",
                name="uq_report_card_notifications_publication_student",
            ),
            sa.CheckConstraint(
                "status IN ('pending', 'sent', 'failed')",
                name="ck_report_card_notifications_status",
            ),
        )
        op.create_index(
            "ix_report_card_notifications_publication_status",
            "report_card_notifications",
            ["publication_id", "status"],
        )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("report_card_notifications"):
        op.drop_index(
            "ix_report_card_notifications_publication_status",
            table_name="report_card_notifications",
        )
        op.drop_table("report_card_notifications")
    if sa.inspect(op.get_bind()).has_table("report_card_publications"):
        op.drop_table("report_card_publications")
