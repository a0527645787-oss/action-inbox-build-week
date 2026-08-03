"""Safe agent execution and manual task completion.

Revision ID: 20260803_0004
Revises: 20260802_0003
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260803_0004"
down_revision = "20260802_0003"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    task_columns = {column["name"] for column in inspector.get_columns("tasks")}
    if "completed_at" not in task_columns:
        op.add_column("tasks", sa.Column("completed_at", sa.DateTime(), nullable=True))
    if "completed_by" not in task_columns:
        op.add_column("tasks", sa.Column("completed_by", sa.String(36), nullable=True))
    tables = set(inspector.get_table_names())
    if "executions" not in tables:
        op.create_table(
            "executions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "task_id",
                sa.Integer(),
                sa.ForeignKey("tasks.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("plan", sa.Text(), nullable=False),
            sa.Column("plan_hash", sa.String(64), nullable=False),
            sa.Column("tool_name", sa.String(100), nullable=False),
            sa.Column("result", sa.Text(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("idempotency_key", sa.String(100), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint(
                "user_id",
                "idempotency_key",
                name="uq_executions_user_idempotency",
            ),
        )
        op.create_index("ix_executions_task_id", "executions", ["task_id"])
        op.create_index("ix_executions_user_id", "executions", ["user_id"])
        op.create_index("ix_executions_status", "executions", ["status"])
    if "execution_events" not in tables:
        op.create_table(
            "execution_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "execution_id",
                sa.Integer(),
                sa.ForeignKey("executions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("event_type", sa.String(50), nullable=False),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("safe_metadata", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_execution_events_execution_id", "execution_events", ["execution_id"])
        op.create_index("ix_execution_events_user_id", "execution_events", ["user_id"])


def downgrade():
    op.drop_table("execution_events")
    op.drop_table("executions")
    op.drop_column("tasks", "completed_by")
    op.drop_column("tasks", "completed_at")
