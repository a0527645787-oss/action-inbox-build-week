"""Durable asynchronous Gmail synchronization.

Revision ID: 20260824_0005
Revises: 20260803_0004
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260824_0005"
down_revision = "20260803_0004"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    credential_columns = {column["name"] for column in inspector.get_columns("gmail_credentials")}
    if "history_id" not in credential_columns:
        op.add_column("gmail_credentials", sa.Column("history_id", sa.String(255), nullable=True))
    if "bootstrap_page_token" not in credential_columns:
        op.add_column("gmail_credentials", sa.Column("bootstrap_page_token", sa.Text(), nullable=True))
    if "gmail_sync_jobs" not in inspector.get_table_names():
        op.create_table(
            "gmail_sync_jobs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("credential_id", sa.Integer(), sa.ForeignKey("gmail_credentials.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("active_slot", sa.Integer(), nullable=True),
            sa.Column("mode", sa.String(30), nullable=False),
            sa.Column("page_token", sa.Text(), nullable=True),
            sa.Column("start_history_id", sa.String(255), nullable=True),
            sa.Column("pending_history_id", sa.String(255), nullable=True),
            sa.Column("pages_listed", sa.Integer(), nullable=False),
            sa.Column("candidates", sa.Integer(), nullable=False),
            sa.Column("details_fetched", sa.Integer(), nullable=False),
            sa.Column("imported", sa.Integer(), nullable=False),
            sa.Column("duplicates", sa.Integer(), nullable=False),
            sa.Column("skipped", sa.Integer(), nullable=False),
            sa.Column("failures", sa.Integer(), nullable=False),
            sa.Column("safe_error", sa.String(80), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("credential_id", "active_slot", name="uq_gmail_sync_active_credential"),
        )
        op.create_index("ix_gmail_sync_jobs_user_id", "gmail_sync_jobs", ["user_id"])
        op.create_index("ix_gmail_sync_jobs_credential_id", "gmail_sync_jobs", ["credential_id"])
        op.create_index("ix_gmail_sync_jobs_status", "gmail_sync_jobs", ["status"])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if "gmail_sync_jobs" in inspector.get_table_names():
        op.drop_table("gmail_sync_jobs")
    credential_columns = {column["name"] for column in inspector.get_columns("gmail_credentials")}
    if "bootstrap_page_token" in credential_columns:
        op.drop_column("gmail_credentials", "bootstrap_page_token")
    if "history_id" in credential_columns:
        op.drop_column("gmail_credentials", "history_id")
