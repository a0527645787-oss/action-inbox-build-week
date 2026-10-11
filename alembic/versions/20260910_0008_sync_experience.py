"""Safe sync phase timings and ownership of new-email analysis; no execution changes."""
from alembic import op
import sqlalchemy as sa

revision = "20260910_0008"
down_revision = "20260910_0007"
branch_labels = depends_on = None


def upgrade():
    for table, columns in {
        "emails": [sa.Column("sync_job_id", sa.Integer(), nullable=True), sa.Column("sync_analysis_attempted", sa.Boolean(), nullable=False, server_default=sa.false())],
        "gmail_sync_jobs": [*[sa.Column(name, sa.Integer(), nullable=False, server_default="0") for name in ("tasks_created", "analysis_failures", "fetch_ms", "storage_ms", "triage_ms", "queue_wait_ms")], sa.Column("phase", sa.String(30), nullable=False, server_default="waiting")],
    }.items():
        existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}
        for column in columns:
            if column.name not in existing:
                op.add_column(table, column)
    if "ix_emails_sync_job_id" not in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("emails")}:
        op.create_index("ix_emails_sync_job_id", "emails", ["sync_job_id"])


def downgrade():
    op.drop_index("ix_emails_sync_job_id", table_name="emails")
    for table, names in {"emails": ["sync_job_id", "sync_analysis_attempted"], "gmail_sync_jobs": ["tasks_created", "analysis_failures", "fetch_ms", "storage_ms", "triage_ms", "queue_wait_ms", "phase"]}.items():
        for name in names:
            op.drop_column(table, name)
