"""Hosted pilot ownership schema.

Revision ID: 20260719_0001
Revises:
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

from app.auth import DEMO_USER_EMAIL, DEMO_USER_ID
from app.database import Base
from app import models  # noqa: F401


revision = "20260719_0001"
down_revision = None
branch_labels = None
depends_on = None


def _upgrade_legacy_sqlite(bind):
    legacy_tables = [name for name in ("tasks", "analyses", "emails", "business_resources") if inspect(bind).has_table(name)]
    bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    for name in legacy_tables:
        op.rename_table(name, f"legacy_{name}")
    Base.metadata.create_all(bind=bind)
    bind.execute(sa.text("INSERT INTO users (id, email, display_name, created_at, updated_at) VALUES (:id, :email, :name, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"), {"id": DEMO_USER_ID, "email": DEMO_USER_EMAIL, "name": "ActionInbox Demo"})
    if "emails" in legacy_tables:
        bind.exec_driver_sql(f"INSERT INTO emails (id,user_id,external_id,sender,subject,received_at,body,source,analyzed) SELECT id,'{DEMO_USER_ID}',external_id,sender,subject,received_at,body,source,analyzed FROM legacy_emails")
    if "business_resources" in legacy_tables:
        bind.exec_driver_sql(f"INSERT INTO business_resources (id,user_id,title,resource_type,content,organization_team,enabled,created_at,updated_at) SELECT id,'{DEMO_USER_ID}',title,resource_type,content,organization_team,enabled,created_at,updated_at FROM legacy_business_resources")
    if "analyses" in legacy_tables:
        columns = {item["name"] for item in inspect(bind).get_columns("legacy_analyses")}
        optional = [name for name in ("structured_result", "source", "model", "error_message") if name in columns]
        target = "id,user_id,email_id,classification,action_required,summary,evidence_quote,evidence_start,evidence_end,suggestion,analyzed_at" + ("," + ",".join(optional) if optional else "")
        source = f"id,'{DEMO_USER_ID}',email_id,classification,action_required,summary,evidence_quote,evidence_start,evidence_end,suggestion,analyzed_at" + ("," + ",".join(optional) if optional else "")
        bind.exec_driver_sql(f"INSERT INTO analyses ({target}) SELECT {source} FROM legacy_analyses")
    if "tasks" in legacy_tables:
        bind.exec_driver_sql(f"INSERT INTO tasks (id,user_id,email_id,title,deadline,deadline_text) SELECT id,'{DEMO_USER_ID}',email_id,title,deadline,deadline_text FROM legacy_tasks")
    for name in ("tasks", "analyses", "business_resources", "emails"):
        if name in legacy_tables:
            op.drop_table(f"legacy_{name}")
    bind.exec_driver_sql("PRAGMA foreign_keys=ON")


def upgrade():
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names()) - {"alembic_version"}
    if bind.dialect.name == "sqlite" and tables and "users" not in tables:
        _upgrade_legacy_sqlite(bind)
    else:
        Base.metadata.create_all(bind=bind)


def downgrade():
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
