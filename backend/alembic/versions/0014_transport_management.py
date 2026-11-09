"""Add transport routes, assignments, and movement logs.

Revision ID: 0014_transport_management
Revises: 0013_library_management
"""
from alembic import op
import sqlalchemy as sa


revision = "0014_transport_management"
down_revision = "0013_library_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "transport_routes" not in existing_tables:
        op.create_table(
            "transport_routes",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("vehicle_number", sa.String(length=48), nullable=False),
            sa.Column("driver_name", sa.String(length=120), nullable=False),
            sa.Column("pickup_point", sa.String(length=160), nullable=False),
            sa.Column("dropoff_point", sa.String(length=160), nullable=False),
            sa.Column("capacity", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("notes", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint("capacity > 0", name="ck_transport_routes_capacity_positive"),
            sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_transport_routes_status"),
        )

    if "transport_assignments" not in existing_tables:
        op.create_table(
            "transport_assignments",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("route_id", sa.String(length=32), nullable=False),
            sa.Column("student_id", sa.String(length=32), nullable=False),
            sa.Column("pickup_point", sa.String(length=160), nullable=False),
            sa.Column("dropoff_point", sa.String(length=160), nullable=False),
            sa.Column("pickup_time", sa.String(length=32)),
            sa.Column("dropoff_time", sa.String(length=32)),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="assigned"),
            sa.Column("notes", sa.String(length=300)),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["route_id"], ["transport_routes.id"]),
            sa.ForeignKeyConstraint(["student_id"], ["students.id"]),
            sa.CheckConstraint("status IN ('assigned', 'active', 'inactive')", name="ck_transport_assignments_status"),
        )
        op.create_index("ix_transport_assignments_route_id", "transport_assignments", ["route_id"])
        op.create_index("ix_transport_assignments_student_id", "transport_assignments", ["student_id"])

    if "transport_logs" not in existing_tables:
        op.create_table(
            "transport_logs",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("route_id", sa.String(length=32), nullable=False),
            sa.Column("student_id", sa.String(length=32), nullable=False),
            sa.Column("event_type", sa.String(length=16), nullable=False),
            sa.Column("event_time", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="on_time"),
            sa.Column("note", sa.Text()),
            sa.Column("created_by_user_id", sa.String(length=32)),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["route_id"], ["transport_routes.id"]),
            sa.ForeignKeyConstraint(["student_id"], ["students.id"]),
            sa.CheckConstraint("event_type IN ('pickup', 'dropoff', 'delay', 'note')", name="ck_transport_logs_event_type"),
            sa.CheckConstraint("status IN ('on_time', 'late', 'missed')", name="ck_transport_logs_status"),
        )
        op.create_index("ix_transport_logs_route_id", "transport_logs", ["route_id"])
        op.create_index("ix_transport_logs_student_id", "transport_logs", ["student_id"])
        op.create_index("ix_transport_logs_event_time", "transport_logs", ["event_time"])


def downgrade() -> None:
    op.drop_index("ix_transport_logs_event_time", table_name="transport_logs")
    op.drop_index("ix_transport_logs_student_id", table_name="transport_logs")
    op.drop_index("ix_transport_logs_route_id", table_name="transport_logs")
    op.drop_table("transport_logs")
    op.drop_index("ix_transport_assignments_student_id", table_name="transport_assignments")
    op.drop_index("ix_transport_assignments_route_id", table_name="transport_assignments")
    op.drop_table("transport_assignments")
    op.drop_table("transport_routes")
