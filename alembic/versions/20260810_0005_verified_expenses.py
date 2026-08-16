"""Verified invoice expense records.

Revision ID: 20260810_0005
Revises: 20260803_0004
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260810_0005"
down_revision = "20260803_0004"
branch_labels = None
depends_on = None


def upgrade():
    if inspect(op.get_bind()).has_table("expense_records"):
        return
    op.create_table(
        "expense_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("supplier", sa.String(255), nullable=False),
        sa.Column("invoice_number", sa.String(100), nullable=False),
        sa.Column("amount", sa.String(100), nullable=False),
        sa.Column("currency", sa.String(20), nullable=False),
        sa.Column("due_date", sa.String(100), nullable=False),
        sa.Column("source_email_id", sa.String(255), nullable=False),
        sa.Column("sheet_row", sa.Integer(), nullable=True),
        sa.Column("verification_status", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "task_id", name="uq_expense_records_user_task"),
        sa.UniqueConstraint("user_id", "supplier", "invoice_number", name="uq_expense_records_user_invoice"),
    )
    op.create_index("ix_expense_records_user_id", "expense_records", ["user_id"])
    op.create_index("ix_expense_records_task_id", "expense_records", ["task_id"])


def downgrade():
    op.drop_table("expense_records")
