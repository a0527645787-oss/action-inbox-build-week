"""Approval-gated verified Google Sheets appends.

Revision ID: 20260824_0006
Revises: 20260824_0005
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260824_0006"
down_revision = "20260824_0005"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    execution_columns = {column["name"] for column in inspector.get_columns("executions")}
    if "attempt_count" not in execution_columns:
        op.add_column("executions", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    if "sheet_proposal_slot" not in execution_columns:
        op.add_column("executions", sa.Column("sheet_proposal_slot", sa.Integer(), nullable=True))
    if "heartbeat_at" not in execution_columns:
        op.add_column("executions", sa.Column("heartbeat_at", sa.DateTime(), nullable=True))
    if "lease_expires_at" not in execution_columns:
        op.add_column("executions", sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
    unique_names = {constraint.get("name") for constraint in inspector.get_unique_constraints("executions")}
    if "uq_executions_sheet_task" not in unique_names:
        with op.batch_alter_table("executions") as batch:
            batch.create_unique_constraint("uq_executions_sheet_task", ["user_id", "task_id", "sheet_proposal_slot"])
    if "sheet_append_records" not in inspector.get_table_names():
        op.create_table(
            "sheet_append_records",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("proposal_id", sa.String(80), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
            sa.Column("execution_id", sa.Integer(), sa.ForeignKey("executions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("spreadsheet_id", sa.String(255), nullable=False),
            sa.Column("sheet_tab", sa.String(255), nullable=False),
            sa.Column("sheet_range", sa.String(255), nullable=False),
            sa.Column("row_hash", sa.String(64), nullable=False),
            sa.Column("verification_status", sa.String(40), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("proposal_id", name="uq_sheet_append_proposal"),
            sa.UniqueConstraint("user_id", "task_id", name="uq_sheet_append_user_task"),
            sa.UniqueConstraint("execution_id", name="uq_sheet_append_execution"),
        )
        op.create_index("ix_sheet_append_records_user_id", "sheet_append_records", ["user_id"])
        op.create_index("ix_sheet_append_records_task_id", "sheet_append_records", ["task_id"])
        op.create_index("ix_sheet_append_records_execution_id", "sheet_append_records", ["execution_id"])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if "sheet_append_records" in inspector.get_table_names():
        op.drop_table("sheet_append_records")
    unique_names = {constraint.get("name") for constraint in inspector.get_unique_constraints("executions")}
    if "uq_executions_sheet_task" in unique_names:
        with op.batch_alter_table("executions") as batch:
            batch.drop_constraint("uq_executions_sheet_task", type_="unique")
    execution_columns = {column["name"] for column in inspector.get_columns("executions")}
    if "lease_expires_at" in execution_columns:
        op.drop_column("executions", "lease_expires_at")
    if "heartbeat_at" in execution_columns:
        op.drop_column("executions", "heartbeat_at")
    if "attempt_count" in execution_columns:
        op.drop_column("executions", "attempt_count")
    if "sheet_proposal_slot" in execution_columns:
        op.drop_column("executions", "sheet_proposal_slot")
