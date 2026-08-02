from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import SESSION_COOKIE_NAME, create_user_session, ensure_demo_user
from app.database import get_db
from app.demo_data import ingest_demo_emails
from app.main import app
from app.models import Email, GmailCredential, Task, User
from app.triage import triage_unanalyzed_emails


def _override_db(db):
    def dependency():
        yield db
    return dependency


def _client(db):
    app.dependency_overrides[get_db] = _override_db(db)
    return TestClient(app, base_url="https://testserver")


def _user(db, suffix):
    user = User(
        id=f"70000000-0000-0000-0000-00000000000{suffix}",
        email=f"user{suffix}@example.test",
        display_name=f"User {suffix}",
        google_subject=f"stable-google-subject-{suffix}",
    )
    db.add(user)
    db.commit()
    return user


def _sign_in(client, db, user):
    client.cookies.set(SESSION_COOKIE_NAME, create_user_session(db, user))


def test_anonymous_browser_sees_only_public_landing_and_private_routes_redirect(db):
    try:
        with _client(db) as client:
            landing = client.get("/")
            assert landing.status_code == 200
            assert "Connect Gmail" in landing.text
            assert "View safe demo" in landing.text
            assert "https://mail.google.com/mail/?view=cm" in landing.text
            assert "to=a0527645787@gmail.com" in landing.text
            assert 'target="_blank"' in landing.text
            for path in (
                "/inbox",
                "/dashboard",
                "/tasks/1",
                "/resources",
                "/gmail/status",
                "/api/inbox/analyze-all",
            ):
                method = client.post if path.startswith("/api/") else client.get
                response = method(path, follow_redirects=False)
                assert response.status_code == 303
                assert response.headers["location"] == "/"
    finally:
        app.dependency_overrides.clear()


def test_safe_demo_exposes_only_synthetic_records_and_never_gmail(db):
    demo = ensure_demo_user(db)
    private = Email(
        user_id=demo.id,
        external_id="gmail:must-not-leak",
        gmail_message_id="must-not-leak",
        sender="private@example.test",
        subject="PRIVATE SUBJECT",
        received_at=datetime.now(UTC).replace(tzinfo=None),
        body="PRIVATE BODY",
        source="gmail",
        analyzed=False,
    )
    db.add(private)
    db.commit()
    try:
        with _client(db) as client:
            response = client.post("/demo", follow_redirects=False)
            assert response.status_code == 303
            assert "tasks_created=3" in response.headers["location"]
            cookie = response.headers["set-cookie"].lower()
            assert "secure" in cookie
            assert "httponly" in cookie
            assert "samesite=lax" in cookie
            inbox = client.get("/inbox")
            assert inbox.status_code == 200
            assert "PRIVATE SUBJECT" not in inbox.text
            assert len(db.scalars(select(Email).where(Email.user_id == demo.id, Email.source == "demo")).all()) == 5
            assert private.analyzed is False
            assert client.get(f"/emails/{private.id}").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_real_sessions_enforce_cross_user_isolation_and_logout(db):
    user_a = _user(db, "1")
    user_b = _user(db, "2")
    email_a = Email(
        user_id=user_a.id, external_id="a", sender="a@example.test", subject="A",
        received_at=datetime.now(UTC).replace(tzinfo=None), body="A", source="gmail",
    )
    email_b = Email(
        user_id=user_b.id, external_id="b", sender="b@example.test", subject="B PRIVATE",
        received_at=datetime.now(UTC).replace(tzinfo=None), body="B PRIVATE", source="gmail",
    )
    db.add_all([email_a, email_b])
    db.commit()
    try:
        with _client(db) as client:
            _sign_in(client, db, user_a)
            inbox = client.get("/inbox")
            assert "B PRIVATE" not in inbox.text
            assert client.get(f"/emails/{email_b.id}").status_code == 404
            logout = client.post("/logout", follow_redirects=False)
            assert logout.status_code == 303
            assert client.get("/inbox", follow_redirects=False).status_code == 303
    finally:
        app.dependency_overrides.clear()


def test_gmail_connections_are_owned_and_demo_is_read_only(db):
    user_a = _user(db, "3")
    user_b = _user(db, "4")
    db.add_all(
        [
            GmailCredential(
                user_id=user_a.id,
                account_email="a@gmail.test",
                encrypted_token="encrypted-a",
                scopes="https://www.googleapis.com/auth/gmail.readonly",
            ),
            GmailCredential(
                user_id=user_b.id,
                account_email="b@gmail.test",
                encrypted_token="encrypted-b",
                scopes="https://www.googleapis.com/auth/gmail.readonly",
            ),
        ]
    )
    db.commit()
    try:
        with _client(db) as client:
            _sign_in(client, db, user_a)
            status = client.get("/gmail/status").json()
            assert status == {"connected": True, "account_email": "a@gmail.test"}
            page = client.get("/gmail")
            assert "a@gmail.test" in page.text
            assert "b@gmail.test" not in page.text

        with _client(db) as demo_client:
            demo_client.post("/demo")
            assert demo_client.get("/resources", follow_redirects=False).headers["location"] == "/"
            assert demo_client.post("/api/inbox/analyze-all", follow_redirects=False).headers["location"] == "/"
    finally:
        app.dependency_overrides.clear()


def test_inbox_sync_button_uses_gmail_sync_and_oauth_rejects_bad_state(db):
    user = _user(db, "5")
    try:
        with _client(db) as client:
            _sign_in(client, db, user)
            inbox = client.get("/inbox")
            assert 'action="/gmail/sync"' in inbox.text
            assert 'action="/api/inbox/analyze-all"' not in inbox.text
            assert client.get("/auth/google/callback?state=wrong&code=code").status_code == 400
            assert client.get("/auth/google/callback?code=code").status_code == 400
    finally:
        app.dependency_overrides.clear()
