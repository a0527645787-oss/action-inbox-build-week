from datetime import UTC, datetime, timedelta
import base64

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.auth import ensure_demo_user, get_current_user
from app.database import get_db
from app.gmail import GMAIL_QUERY, GMAIL_SCOPE, encrypt_tokens, sync_gmail
from app.main import app
from app.models import Email, GmailCredential, Task


class FakeResponse:
    def __init__(self, data, status=200): self.data, self.status_code = data, status
    def json(self): return self.data
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError("HTTP failure")


class GmailClient:
    def __init__(self): self.calls = []
    def get(self, url, headers=None, params=None):
        self.calls.append(("GET", url, params))
        if url.endswith("/messages"):
            return FakeResponse({"messages": [{"id": "gmail-1", "threadId": "thread-1"}]})
        body = "For vendor renewal, please send your current W-9 form and proof of insurance. We need both documents by July 24, 2026."
        encoded = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
        return FakeResponse({"id": "gmail-1", "threadId": "thread-1", "labelIds": ["INBOX", "UNREAD"],
            "internalDate": "1784682000000", "payload": {"mimeType": "text/plain", "body": {"data": encoded},
            "headers": [{"name": "From", "value": "Vendor <vendor@example.test>"}, {"name": "Subject", "value": "Vendor renewal"}]}})
    def post(self, *args, **kwargs): raise AssertionError("Gmail sync must not POST to Google")


def _override_db(db):
    def dependency():
        yield db
    return dependency


def _token(monkeypatch):
    key = Fernet.generate_key().decode(); monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    return encrypt_tokens({"access_token": "test-access", "refresh_token": "test-refresh", "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()})


def test_gmail_sync_is_bounded_read_only_and_idempotent(db, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    user = ensure_demo_user(db)
    credential = GmailCredential(user_id=user.id, account_email="pilot@example.test", encrypted_token=_token(monkeypatch), scopes=GMAIL_SCOPE)
    db.add(credential); db.commit()
    client = GmailClient()
    first = sync_gmail(db, user, credential, client=client)
    assert first.scope == GMAIL_QUERY
    assert (first.candidates, first.new_messages, first.tasks_created) == (1, 1, 1)
    list_call = client.calls[0]
    assert list_call[2] == {"labelIds": "INBOX", "q": GMAIL_QUERY, "maxResults": 25}
    assert all(method == "GET" for method, _, _ in client.calls)
    email = db.scalar(select(Email).where(Email.gmail_message_id == "gmail-1"))
    assert email and email.source == "gmail"
    second = sync_gmail(db, user, credential, client=client)
    assert second.new_messages == 0 and second.tasks_created == 0
    assert db.scalar(select(func.count()).select_from(Email).where(Email.gmail_message_id == "gmail-1")) == 1
    assert db.scalar(select(func.count()).select_from(Task).where(Task.email_id == email.id)) == 1

    duplicate = Email(user_id=user.id, external_id="gmail:duplicate", gmail_message_id="gmail-1",
                      sender="x", subject="x", received_at=datetime.now(UTC).replace(tzinfo=None), body="x")
    db.add(duplicate)
    try:
        db.commit()
        raise AssertionError("duplicate Gmail message ID must be rejected")
    except IntegrityError:
        db.rollback()


def test_mcp_requires_auth_and_all_tools_are_read_only(db, monkeypatch):
    user = ensure_demo_user(db)
    monkeypatch.setenv("MCP_ACCESS_TOKEN", "mcp-test-token")
    monkeypatch.setenv("MCP_USER_ID", user.id)
    app.dependency_overrides[get_db] = _override_db(db)
    try:
        client = TestClient(app)
        assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401
        response = client.post("/mcp", headers={"Authorization": "Bearer mcp-test-token"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert response.status_code == 200
        tools = response.json()["result"]["tools"]
        assert {item["name"] for item in tools} == {"list_actioninbox_tasks", "get_actioninbox_task", "prepare_task_execution"}
        assert all(item["annotations"]["readOnlyHint"] is True for item in tools)
        assert all(item["annotations"]["destructiveHint"] is False for item in tools)
    finally:
        app.dependency_overrides.clear()


def test_gmail_page_discloses_exact_scope(db):
    user = ensure_demo_user(db)
    app.dependency_overrides[get_db] = _override_db(db)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        response = TestClient(app).get("/gmail")
        assert response.status_code == 200
        assert GMAIL_QUERY in response.text
        assert GMAIL_SCOPE in response.text
        assert "25 per sync" in response.text and "20 newly created tasks" in response.text
    finally:
        app.dependency_overrides.clear()
