"""Per-user Google identities and server-side sessions.

Revision ID: 20260802_0003
Revises: 20260722_0002
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260802_0003"
down_revision = "20260722_0002"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "google_subject" not in user_columns:
        op.add_column("users", sa.Column("google_subject", sa.String(255), nullable=True))
        op.create_unique_constraint("uq_users_google_subject", "users", ["google_subject"])

    state_columns = {column["name"]: column for column in inspector.get_columns("gmail_oauth_states")}
    if state_columns["user_id"]["nullable"] is False:
        with op.batch_alter_table("gmail_oauth_states") as batch_op:
            batch_op.alter_column(
                "user_id",
                existing_type=sa.String(36),
                nullable=True,
            )

    if "user_sessions" not in inspector.get_table_names():
        op.create_table(
            "user_sessions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if "user_sessions" in inspector.get_table_names():
        op.drop_table("user_sessions")
    state_columns = {column["name"]: column for column in inspector.get_columns("gmail_oauth_states")}
    if state_columns["user_id"]["nullable"] is True:
        op.execute(sa.text("DELETE FROM gmail_oauth_states WHERE user_id IS NULL"))
        with op.batch_alter_table("gmail_oauth_states") as batch_op:
            batch_op.alter_column(
                "user_id",
                existing_type=sa.String(36),
                nullable=False,
            )
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "google_subject" in user_columns:
        op.drop_constraint("uq_users_google_subject", "users", type_="unique")
        op.drop_column("users", "google_subject")
