"""Generic configured tables; legacy execution and receipt rows remain untouched."""
import json
import os
from datetime import datetime, UTC

from alembic import op
import sqlalchemy as sa

revision = "20260910_0007"
down_revision = "20260824_0006"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    # The initial historical migration uses current Base.metadata for fresh installs.
    def create_table(name, *columns):
        if sa.inspect(bind).has_table(name):
            return sa.Table(name, sa.MetaData(), autoload_with=bind)
        return op.create_table(name, *columns)

    if "append_attempted_at" not in {c["name"] for c in inspector.get_columns("executions")}:
        op.add_column("executions", sa.Column("append_attempted_at", sa.DateTime(), nullable=True))
    tables = create_table("table_destinations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connector_kind", sa.String(40), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("target", sa.String(160), nullable=False),
        sa.Column("tab_name", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("schema_snapshot", sa.Text(), nullable=False),
        sa.Column("column_mapping", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "target", "tab_name", name="uq_table_destination_target"))
    if "ix_table_destinations_user_id" not in {i["name"] for i in sa.inspect(bind).get_indexes("table_destinations")}:
        op.create_index("ix_table_destinations_user_id", "table_destinations", ["user_id"])
    create_table("table_append_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("action_key", sa.String(80), nullable=False, unique=True),
        sa.Column("execution_id", sa.Integer(), sa.ForeignKey("executions.id"), nullable=False, unique=True),
        sa.Column("destination_id", sa.Integer(), sa.ForeignKey("table_destinations.id"), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("row_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    # The old deployment-wide destination was available to connected personal users.
    # Materialize that metadata once. Future users explicitly configure their own tables.
    target = os.getenv("ACTIONINBOX_SHEET_ID", "").strip()
    tab = os.getenv("ACTIONINBOX_SHEET_TAB", "")
    if target and tab:
        bind = op.get_bind()
        users = bind.execute(sa.text("SELECT id FROM users WHERE id <> '00000000-0000-0000-0000-000000000001'"))
        headers = ["Created At", "Proposal ID", "Supplier", "Invoice Number", "Amount", "Currency", "Due Date", "Status", "Source Email ID", "ActionInbox Task ID", "Verification Status"]
        mapping = ["created_at", "idempotency_key", "supplier", "invoice_number", "amount", "currency", "due_date", "status", "source_email_id", "task_id", "verification_status"]
        for user_id, in users:
            if bind.execute(sa.select(tables.c.id).where(tables.c.user_id == user_id, tables.c.target == target, tables.c.tab_name == tab)).first():
                continue
            bind.execute(tables.insert().values(user_id=user_id, connector_kind="google_sheets",
                display_name="Expenses", target=target, tab_name=tab,
                enabled=os.getenv("ACTIONINBOX_SHEETS_ENABLED", "false").lower() == "true",
                schema_snapshot=json.dumps(headers), column_mapping=json.dumps(mapping),
                created_at=datetime.now(UTC).replace(tzinfo=None)))


def downgrade():
    op.drop_table("table_append_records")
    op.drop_table("table_destinations")
    op.drop_column("executions", "append_attempted_at")
