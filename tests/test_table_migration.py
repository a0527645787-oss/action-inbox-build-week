import json
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


def test_migrates_expenses_metadata_without_changing_legacy_executions(tmp_path, monkeypatch):
    url = "sqlite:///" + (tmp_path / "migration.db").as_posix()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ACTIONINBOX_SHEET_ID", "existing-shared-target")
    monkeypatch.setenv("ACTIONINBOX_SHEET_TAB", "Expenses ")
    monkeypatch.setenv("ACTIONINBOX_SHEETS_ENABLED", "true")
    config = Config("alembic.ini")
    command.upgrade(config, "20260824_0006")
    engine = create_engine(url)
    with engine.begin() as conn:
        # 0001 imports current metadata. Remove only the new empty structures in this
        # isolated test database to exercise the actual production upgrade path.
        conn.execute(text("DROP TABLE table_append_records"))
        conn.execute(text("DROP TABLE table_destinations"))
        conn.execute(text("ALTER TABLE executions DROP COLUMN append_attempted_at"))
        conn.execute(text("INSERT INTO users (id,email,display_name,created_at,updated_at) VALUES ('owner','owner@example.test','Owner',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
        conn.execute(text("INSERT INTO emails (id,user_id,external_id,sender,subject,received_at,body,source,analyzed) VALUES (99,'owner','old','sender','old',CURRENT_TIMESTAMP,'old','gmail',1)"))
        conn.execute(text("INSERT INTO tasks (id,user_id,email_id,title) VALUES (99,'owner',99,'old')"))
        for number, status in [(91, 'completed_verified'), (92, 'failed')]:
            conn.execute(text("INSERT INTO executions (id,task_id,user_id,status,plan,plan_hash,tool_name,idempotency_key,created_at,attempt_count) VALUES (:id,99,'owner',:status,:plan,'frozen','append_verified_invoice_row',:key,CURRENT_TIMESTAMP,1)"),
                         {"id": number, "status": status, "plan": json.dumps({"legacy": number}), "key": str(number)})
        old = conn.execute(text("SELECT * FROM executions ORDER BY id")).mappings().all()
    command.upgrade(config, "head")
    with engine.connect() as conn:
        dest = conn.execute(text("SELECT * FROM table_destinations WHERE user_id='owner'")).mappings().one()
        assert dest["display_name"] == "Expenses" and dest["tab_name"] == "Expenses "
        assert dest["target"] == "existing-shared-target" and dest["enabled"]
        assert json.loads(dest["column_mapping"])[1] == "idempotency_key"
        updated = conn.execute(text("SELECT * FROM executions ORDER BY id")).mappings().all()
        for before, after in zip(old, updated):
            assert dict(before) == {key: after[key] for key in before}
        assert conn.scalar(text("SELECT COUNT(*) FROM table_append_records")) == 0
