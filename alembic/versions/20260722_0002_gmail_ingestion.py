"""Bounded read-only Gmail ingestion.

Revision ID: 20260722_0002
Revises: 20260719_0001
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260722_0002"
down_revision = "20260719_0001"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    email_columns = {column["name"] for column in inspector.get_columns("emails")}
    if "gmail_message_id" not in email_columns:
        op.add_column("emails", sa.Column("gmail_message_id", sa.String(255), nullable=True))
    if "gmail_thread_id" not in email_columns:
        op.add_column("emails", sa.Column("gmail_thread_id", sa.String(255), nullable=True))
    unique_constraints = {constraint["name"] for constraint in inspector.get_unique_constraints("emails")}
    if "uq_emails_user_gmail_message" not in unique_constraints:
        with op.batch_alter_table("emails") as batch_op:
            batch_op.create_unique_constraint(
                "uq_emails_user_gmail_message",
                ["user_id", "gmail_message_id"],
            )
    if "gmail_oauth_states" not in inspector.get_table_names():
        op.create_table(
            "gmail_oauth_states",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("state_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("used_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_gmail_oauth_states_user_id", "gmail_oauth_states", ["user_id"])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if "gmail_oauth_states" in inspector.get_table_names():
        op.drop_table("gmail_oauth_states")
    email_columns = {column["name"] for column in inspector.get_columns("emails")}
    unique_constraints = {constraint["name"] for constraint in inspector.get_unique_constraints("emails")}
    if "uq_emails_user_gmail_message" in unique_constraints:
        with op.batch_alter_table("emails") as batch_op:
            batch_op.drop_constraint("uq_emails_user_gmail_message", type_="unique")
    if "gmail_thread_id" in email_columns:
        op.drop_column("emails", "gmail_thread_id")
    if "gmail_message_id" in email_columns:
        op.drop_column("emails", "gmail_message_id")
