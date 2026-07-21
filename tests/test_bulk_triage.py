from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.auth import get_current_user
from app.database import get_db
from app.demo_data import DEMO_EMAILS, load_demo_emails
from app.main import app
from app.models import Analysis, Email, Task, User


def _client(db, user):
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_bulk_triage_analyzes_owned_inbox_and_is_idempotent(db):
    emails = load_demo_emails(db)
    user = db.get(User, emails[0].user_id)
    other = User(id="50000000-0000-0000-0000-000000000005", email="other-triage@example.test", display_name="Other")
    db.add(other)
    db.commit()
    other_email = Email(**DEMO_EMAILS[0], user_id=other.id, source="test")
    db.add(other_email)
    db.commit()

    try:
        with _client(db, user) as client:
            response = client.post("/api/inbox/analyze-all", follow_redirects=False)
            assert response.status_code == 303
            assert response.headers["location"] == "/dashboard?emails_checked=5&tasks_created=3"
            assert db.scalar(select(func.count()).select_from(Analysis).where(Analysis.user_id == user.id)) == 5
            assert db.scalar(select(func.count()).select_from(Task).where(Task.user_id == user.id)) == 3
            assert other_email.analyzed is False
            assert db.scalar(select(Analysis).where(Analysis.email_id == other_email.id)) is None

            non_actionable_ids = [email.id for email in emails if email.external_id in {"demo-info", "demo-newsletter"}]
            assert db.scalar(select(func.count()).select_from(Task).where(Task.email_id.in_(non_actionable_ids))) == 0

            second = client.post("/api/inbox/analyze-all", follow_redirects=False)
            assert second.headers["location"] == "/dashboard?emails_checked=0&tasks_created=0"
            assert db.scalar(select(func.count()).select_from(Analysis).where(Analysis.user_id == user.id)) == 5
            assert db.scalar(select(func.count()).select_from(Task).where(Task.user_id == user.id)) == 3
    finally:
        app.dependency_overrides.clear()


def test_bulk_triage_is_post_only(db):
    emails = load_demo_emails(db)
    user = db.get(User, emails[0].user_id)
    try:
        with _client(db, user) as client:
            assert client.get("/api/inbox/analyze-all").status_code == 405
            assert db.scalar(select(func.count()).select_from(Analysis)) == 0
    finally:
        app.dependency_overrides.clear()


def test_single_email_analyze_and_reanalyze_still_work(db):
    emails = load_demo_emails(db)
    user = db.get(User, emails[0].user_id)
    invoice = next(email for email in emails if email.external_id == "demo-invoice")
    try:
        with _client(db, user) as client:
            first = client.post(f"/api/emails/{invoice.id}/analyze", follow_redirects=False)
            assert first.status_code == 303
            assert db.scalar(select(func.count()).select_from(Analysis)) == 1
            assert db.scalar(select(func.count()).select_from(Task)) == 1

            second = client.post(f"/api/emails/{invoice.id}/reanalyze", follow_redirects=False)
            assert second.status_code == 303
            assert db.scalar(select(func.count()).select_from(Analysis)) == 1
            assert db.scalar(select(func.count()).select_from(Task)) == 1
    finally:
        app.dependency_overrides.clear()
