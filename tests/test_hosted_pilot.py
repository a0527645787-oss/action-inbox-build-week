from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.analysis import analyze_email
from app.auth import DEMO_USER_ID, get_current_user
from app.database import get_db
from app.demo_data import DEMO_EMAILS, load_demo_emails
from app.main import app
from app.models import Analysis, BusinessResource, Email, GmailCredential, Task, User


def _user(identifier: str, email: str) -> User:
    return User(id=identifier, email=email, display_name=email.split("@")[0])


def _invoice(user: User) -> Email:
    return Email(**DEMO_EMAILS[0], source="test", user_id=user.id)


def test_production_mode_fails_closed_without_authentication(db, monkeypatch):
    monkeypatch.delenv("LOCAL_DEMO_AUTH_ENABLED", raising=False)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            assert client.get("/inbox").status_code == 503
            assert client.get("/health").status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_routes_and_analysis_are_isolated_between_users(db):
    user_a = _user("10000000-0000-0000-0000-000000000001", "a@example.test")
    user_b = _user("20000000-0000-0000-0000-000000000002", "b@example.test")
    db.add_all([user_a, user_b])
    db.commit()
    invoice_a, invoice_b = _invoice(user_a), _invoice(user_b)
    db.add_all([invoice_a, invoice_b])
    db.add_all([
        BusinessResource(user_id=user_a.id, title="Invoice policy A", resource_type="policy", content="Invoices require approval.", enabled=True),
        BusinessResource(user_id=user_b.id, title="Invoice policy B", resource_type="policy", content="Invoices require approval.", enabled=True),
    ])
    db.commit()
    analyze_email(db, invoice_a)
    analyze_email(db, invoice_b)
    resource_b = db.scalar(select(BusinessResource).where(BusinessResource.user_id == user_b.id))
    task_b = db.scalar(select(Task).where(Task.user_id == user_b.id))
    db.add_all([
        GmailCredential(user_id=user_a.id, account_email="a@gmail.test", encrypted_token="encrypted-a", scopes="gmail.readonly"),
        GmailCredential(user_id=user_b.id, account_email="b@gmail.test", encrypted_token="encrypted-b", scopes="gmail.readonly"),
    ])
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user_a
    try:
        with TestClient(app) as client:
            assert client.get(f"/emails/{invoice_b.id}").status_code == 404
            assert client.get(f"/tasks/{task_b.id}").status_code == 404
            assert client.get(f"/resources/{resource_b.id}").status_code == 404
            assert client.post(f"/api/emails/{invoice_b.id}/reanalyze").status_code == 404
            inbox = client.get("/inbox").text
            dashboard = client.get("/dashboard").text
            assert f'/emails/{invoice_b.id}' not in inbox
            assert f'/tasks/{task_b.id}' not in dashboard
    finally:
        app.dependency_overrides.clear()

    assert {item.user_id for item in db.scalars(select(Analysis)).all()} == {user_a.id, user_b.id}
    assert {item.user_id for item in db.scalars(select(GmailCredential)).all()} == {user_a.id, user_b.id}


def test_database_rejects_cross_user_task_reference(db):
    user_a = _user("30000000-0000-0000-0000-000000000003", "c@example.test")
    user_b = _user("40000000-0000-0000-0000-000000000004", "d@example.test")
    db.add_all([user_a, user_b])
    db.commit()
    email = _invoice(user_a)
    db.add(email)
    db.commit()
    db.add(Task(user_id=user_b.id, email_id=email.id, title="Cross-owner task"))
    try:
        db.commit()
        raise AssertionError("cross-user composite foreign key was not enforced")
    except IntegrityError:
        db.rollback()


def _alembic_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_empty_sqlite_database_migrates_to_head(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = (tmp_path / "empty.sqlite3").as_posix()
    config = _alembic_config(f"sqlite:///{path}")
    command.upgrade(config, "head")
    from sqlalchemy import create_engine
    migrated = create_engine(f"sqlite:///{path}")
    inspector = inspect(migrated)
    assert {"users", "emails", "analyses", "tasks", "business_resources", "gmail_credentials"} <= set(inspector.get_table_names())
    assert "user_id" in {column["name"] for column in inspector.get_columns("emails")}
    for table in ("emails", "analyses", "tasks", "business_resources", "gmail_credentials"):
        assert "user_id" in {column["name"] for column in inspector.get_columns(table)}
    command.check(config)


def test_legacy_sqlite_rows_are_backfilled_to_demo_user(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = (tmp_path / "legacy.sqlite3").as_posix()
    url = f"sqlite:///{path}"
    from sqlalchemy import create_engine
    legacy = create_engine(url)
    with legacy.begin() as connection:
        connection.execute(text("CREATE TABLE emails (id INTEGER PRIMARY KEY, external_id VARCHAR(100) UNIQUE, sender VARCHAR(255), subject VARCHAR(255), received_at DATETIME, body TEXT, source VARCHAR(30), analyzed BOOLEAN)"))
        connection.execute(text("INSERT INTO emails VALUES (1, 'legacy-email', 'sender@example.test', 'Legacy', CURRENT_TIMESTAMP, 'Body', 'demo', 0)"))
    command.upgrade(_alembic_config(url), "head")
    with legacy.connect() as connection:
        assert connection.scalar(text("SELECT user_id FROM emails WHERE id=1")) == DEMO_USER_ID
        assert connection.scalar(text("SELECT COUNT(*) FROM users WHERE id=:id"), {"id": DEMO_USER_ID}) == 1
    assert "legacy_emails" not in inspect(legacy).get_table_names()
