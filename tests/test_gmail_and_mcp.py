from datetime import UTC, datetime, timedelta
import base64

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.analysis import analyze_email, fallback_analysis
from app.auth import ensure_demo_user, get_current_user
from app.database import get_db
from app.gmail import (
    GMAIL_QUERY,
    GMAIL_SCOPE,
    GmailSyncError,
    claim_gmail_sync_job,
    complete_oauth,
    enqueue_gmail_sync,
    encrypt_tokens,
    run_gmail_sync_job,
)
from app.main import app
from app.models import Analysis, Email, GmailCredential, GmailOAuthState, GmailSyncJob, Task, User, utcnow
from app.triage import triage_unanalyzed_emails


class FakeResponse:
    def __init__(self, data, status=200, headers=None): self.data, self.status_code, self.headers = data, status, headers or {}
    def json(self): return self.data
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError("HTTP failure")


class GmailClient:
    def __init__(self): self.calls = []
    def get(self, url, headers=None, params=None):
        self.calls.append(("GET", url, params))
        if url.endswith("/profile"):
            return FakeResponse({"historyId": "history-2"})
        if url.endswith("/messages"):
            return FakeResponse({"messages": [{"id": "gmail-1", "threadId": "thread-1"}]})
        body = "For vendor renewal, please send your current W-9 form and proof of insurance. We need both documents by July 24, 2026."
        encoded = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
        return FakeResponse({"id": "gmail-1", "threadId": "thread-1", "labelIds": ["INBOX", "UNREAD"],
            "internalDate": "1784682000000", "payload": {"mimeType": "text/plain", "body": {"data": encoded},
            "headers": [{"name": "From", "value": "Vendor <vendor@example.test>"}, {"name": "Subject", "value": "Vendor renewal"}]}})
    def post(self, *args, **kwargs): raise AssertionError("Gmail sync must not POST to Google")


class OAuthClient:
    def __init__(self, scopes=None, identity=None):
        self.scopes = scopes or (
            f"openid https://www.googleapis.com/auth/userinfo.email {GMAIL_SCOPE}"
        )
        self.identity = identity or {
            "sub": "stable-google-subject",
            "email": "connected@example.test",
            "name": "Connected User",
        }

    def post(self, url, data=None):
        return FakeResponse(
            {
                "access_token": "test-access",
                "refresh_token": "test-refresh",
                "expires_in": 3600,
                "scope": self.scopes,
            }
        )

    def get(self, url, headers=None):
        return FakeResponse(self.identity)


def _override_db(db):
    def dependency():
        yield db
    return dependency


def _token(monkeypatch):
    key = Fernet.generate_key().decode(); monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    return encrypt_tokens({"access_token": "test-access", "refresh_token": "test-refresh", "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()})


def _personal_user(db, suffix="1"):
    user = User(
        id=f"90000000-0000-0000-0000-00000000000{suffix}",
        email=f"pilot{suffix}@example.test",
        display_name=f"Pilot {suffix}",
        google_subject=f"google-subject-{suffix}",
    )
    db.add(user)
    db.commit()
    return user


def _oauth_state(db, state):
    db.add(
        GmailOAuthState(
            state_hash=__import__("hashlib").sha256(state.encode()).hexdigest(),
            expires_at=utcnow() + timedelta(minutes=5),
        )
    )
    db.commit()


def test_gmail_sync_job_is_read_only_ingestion_and_idempotent(db, monkeypatch):
    user = _personal_user(db)
    credential = GmailCredential(user_id=user.id, account_email="pilot@example.test", encrypted_token=_token(monkeypatch), scopes=GMAIL_SCOPE)
    db.add(credential); db.commit()
    client = GmailClient()
    job = enqueue_gmail_sync(db, user, credential)
    assert enqueue_gmail_sync(db, user, credential).id == job.id
    run_gmail_sync_job(db, job, client=client)
    db.refresh(job)
    assert job.status == "succeeded" and job.imported == 1
    list_call = client.calls[0]
    assert list_call[2] == {"labelIds": "INBOX", "q": GMAIL_QUERY, "maxResults": 100}
    assert all(method == "GET" for method, _, _ in client.calls)
    email = db.scalar(select(Email).where(Email.gmail_message_id == "gmail-1"))
    assert email and email.source == "gmail" and email.analyzed is False
    assert email.analysis is None and email.task is None
    second = enqueue_gmail_sync(db, user, credential)
    run_gmail_sync_job(db, second, client=client)
    assert db.scalar(select(func.count()).select_from(Email).where(Email.gmail_message_id == "gmail-1")) == 1
    assert db.scalar(select(func.count()).select_from(Task).where(Task.email_id == email.id)) == 0

    duplicate = Email(user_id=user.id, external_id="gmail:duplicate", gmail_message_id="gmail-1",
                      sender="x", subject="x", received_at=datetime.now(UTC).replace(tzinfo=None), body="x")
    db.add(duplicate)
    try:
        db.commit()
        raise AssertionError("duplicate Gmail message ID must be rejected")
    except IntegrityError:
        db.rollback()


def test_gmail_sync_route_returns_202_and_same_active_job(db, monkeypatch):
    user = _personal_user(db)
    credential = GmailCredential(user_id=user.id, account_email="pilot@example.test",
                                 encrypted_token=_token(monkeypatch), scopes=GMAIL_SCOPE)
    db.add(credential); db.commit()
    app.dependency_overrides[get_db] = _override_db(db)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        client = TestClient(app)
        first = client.post("/gmail/sync")
        second = client.post("/gmail/sync")
        assert first.status_code == 202 and second.status_code == 202
        assert first.json()["job_id"] == second.json()["job_id"]
        assert first.headers["location"] == first.json()["status_url"]
        status = client.get(first.json()["status_url"])
        assert status.status_code == 200 and status.json()["status"] == "queued"
    finally:
        app.dependency_overrides.clear()


def test_gmail_worker_paginates_then_uses_history_cursor(db, monkeypatch):
    class PagedClient(GmailClient):
        def get(self, url, headers=None, params=None):
            self.calls.append(("GET", url, params))
            if url.endswith("/messages"):
                if params.get("pageToken") == "page-2":
                    return FakeResponse({"messages": [{"id": "gmail-2"}]})
                return FakeResponse({"messages": [{"id": "gmail-1"}], "nextPageToken": "page-2"})
            if url.endswith("/history"):
                return FakeResponse({"historyId": "history-3", "history": [{"messagesAdded": [{"message": {"id": "gmail-3"}}]}]})
            if url.endswith("/profile"):
                return FakeResponse({"historyId": "history-2"})
            message_id = url.rsplit("/", 1)[-1]
            body = f"Synthetic read-only Gmail message {message_id}."
            encoded = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
            return FakeResponse({"id": message_id, "threadId": "thread", "labelIds": ["INBOX"], "internalDate": "1784682000000",
                "payload": {"mimeType": "text/plain", "body": {"data": encoded}, "headers": []}})

    user = _personal_user(db)
    credential = GmailCredential(user_id=user.id, account_email="pilot@example.test", encrypted_token=_token(monkeypatch), scopes=GMAIL_SCOPE)
    db.add(credential); db.commit()
    client = PagedClient()
    bootstrap = enqueue_gmail_sync(db, user, credential)
    run_gmail_sync_job(db, bootstrap, client=client)
    db.refresh(credential); db.refresh(bootstrap)
    assert bootstrap.pages_listed == 2 and bootstrap.imported == 2
    assert credential.history_id == "history-2"

    incremental = enqueue_gmail_sync(db, user, credential)
    assert incremental.mode == "history" and incremental.start_history_id == "history-2"
    run_gmail_sync_job(db, incremental, client=client)
    db.refresh(credential)
    assert credential.history_id == "history-3"
    assert db.scalar(select(func.count()).select_from(Email).where(Email.user_id == user.id)) == 3
    history_calls = [call for call in client.calls if call[1].endswith("/history")]
    assert history_calls[0][2]["startHistoryId"] == "history-2"


def test_expired_worker_lease_is_recoverably_claimed(db, monkeypatch):
    user = _personal_user(db)
    credential = GmailCredential(user_id=user.id, account_email="pilot@example.test", encrypted_token=_token(monkeypatch), scopes=GMAIL_SCOPE)
    db.add(credential); db.commit()
    job = enqueue_gmail_sync(db, user, credential)
    job.status = "running"
    job.lease_expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    claimed = claim_gmail_sync_job(db)
    assert claimed.id == job.id
    assert claimed.status == "running" and claimed.attempts == 1
    assert claimed.heartbeat_at is not None and claimed.lease_expires_at > claimed.heartbeat_at


def test_transient_detail_failure_is_bounded_and_isolated(db, monkeypatch):
    class IsolatedFailureClient(GmailClient):
        def __init__(self):
            super().__init__()
            self.failed_attempts = 0

        def get(self, url, headers=None, params=None):
            if url.endswith("/messages"):
                return FakeResponse({"messages": [{"id": "gmail-good"}, {"id": "gmail-fail"}]})
            if url.endswith("/profile"):
                return FakeResponse({"historyId": "history-2"})
            if url.endswith("gmail-fail"):
                self.failed_attempts += 1
                return httpx.Response(503, request=httpx.Request("GET", url))
            message_id = url.rsplit("/", 1)[-1]
            encoded = base64.urlsafe_b64encode(b"Safe body").decode().rstrip("=")
            return FakeResponse({"id": message_id, "labelIds": ["INBOX"], "payload": {"mimeType": "text/plain", "body": {"data": encoded}, "headers": []}})

    monkeypatch.setattr("app.gmail.time.sleep", lambda _: None)
    user = _personal_user(db)
    credential = GmailCredential(user_id=user.id, account_email="pilot@example.test", encrypted_token=_token(monkeypatch), scopes=GMAIL_SCOPE)
    db.add(credential); db.commit()
    client = IsolatedFailureClient()
    job = enqueue_gmail_sync(db, user, credential)
    run_gmail_sync_job(db, job, client=client)
    db.refresh(job)
    assert job.status == "partial" and job.imported == 1 and job.failures == 1
    assert job.safe_error == "MESSAGE_FAILURES" and client.failed_attempts == 4
    assert db.scalar(select(Email).where(Email.gmail_message_id == "gmail-good")).analyzed is False


def test_supported_synthetic_email_still_uses_deterministic_fallback(db, monkeypatch):
    from app.demo_data import ingest_demo_emails

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    user = ensure_demo_user(db)
    ingest_demo_emails(db, user)
    email = db.scalar(select(Email).where(Email.user_id == user.id, Email.external_id == "demo-documents"))
    analysis = analyze_email(db, email)
    assert analysis.source == "demo_fallback"
    assert email.task is not None


def test_public_mcp_is_no_auth_read_only_and_synthetic_demo_only(db, monkeypatch):
    from app.demo_data import ingest_demo_emails

    user = ensure_demo_user(db)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ingest_demo_emails(db, user)
    triage_unanalyzed_emails(db, user.id)
    demo_task = db.scalar(select(Task).join(Task.email).where(Email.source == "demo"))

    private_email = Email(user_id=user.id, external_id="gmail:private", gmail_message_id="private-gmail-id",
                          sender="private@example.test", subject="PRIVATE GMAIL SUBJECT",
                          received_at=datetime.now(UTC).replace(tzinfo=None), body="PRIVATE GMAIL BODY",
                          source="gmail", analyzed=True)
    db.add(private_email); db.flush()
    db.add(Analysis(user_id=user.id, email_id=private_email.id, classification="action_required",
                    action_required=True, summary="PRIVATE GMAIL SUMMARY",
                    structured_result=demo_task.email.analysis.structured_result, source="live_gpt"))
    private_task = Task(user_id=user.id, email_id=private_email.id, title="PRIVATE GMAIL TASK")
    db.add(private_task); db.commit(); db.refresh(private_task)

    monkeypatch.setenv("MCP_ACCESS_TOKEN", "mcp-test-token")
    monkeypatch.setenv("MCP_USER_ID", user.id)
    app.dependency_overrides[get_db] = _override_db(db)
    try:
        client = TestClient(app)
        initialize = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
        assert initialize.status_code == 200
        assert initialize.json()["result"]["capabilities"]["tools"] == {"listChanged": False}

        response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert response.status_code == 200
        tools = response.json()["result"]["tools"]
        assert {item["name"] for item in tools} == {"list_actioninbox_tasks", "get_actioninbox_task", "prepare_task_execution"}
        assert all(item["annotations"]["readOnlyHint"] is True for item in tools)
        assert all(item["annotations"]["destructiveHint"] is False for item in tools)
        assert all(item["annotations"]["idempotentHint"] is True for item in tools)
        assert all(item["annotations"]["openWorldHint"] is False for item in tools)
        assert all(item["securitySchemes"] == [{"type": "noauth"}] for item in tools)

        public_list = client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                  "params": {"name": "list_actioninbox_tasks", "arguments": {}}})
        public_text = public_list.text
        assert public_list.status_code == 200 and "PRIVATE GMAIL" not in public_text
        assert demo_task.title in public_text

        hidden = client.post("/mcp", json={"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                             "params": {"name": "get_actioninbox_task", "arguments": {"task_id": private_task.id}}})
        assert hidden.json()["error"]["message"] == "Task not found"

        prepared = client.post("/mcp", json={"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                               "params": {"name": "prepare_task_execution", "arguments": {"task_id": demo_task.id}}})
        package = prepared.json()["result"]["structuredContent"]
        assert package["review_required"] is True and package["executed"] is False

        assert client.post("/mcp", headers={"Authorization": "Bearer wrong"},
                           json={"jsonrpc": "2.0", "id": 6, "method": "tools/list"}).status_code == 200
        authenticated = client.post("/mcp", headers={"Authorization": "Bearer mcp-test-token"},
                                    json={"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                          "params": {"name": "get_actioninbox_task", "arguments": {"task_id": private_task.id}}})
        assert authenticated.status_code == 200 and authenticated.json()["error"]["message"] == "Task not found"
    finally:
        app.dependency_overrides.clear()


def test_gmail_page_discloses_exact_scope(db, monkeypatch):
    user = _personal_user(db)
    db.add(GmailCredential(
        user_id=user.id,
        account_email="pilot@example.test",
        encrypted_token=_token(monkeypatch),
        scopes=GMAIL_SCOPE,
    ))
    db.commit()
    app.dependency_overrides[get_db] = _override_db(db)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        response = TestClient(app).get("/gmail")
        assert response.status_code == 200
        assert GMAIL_QUERY in response.text
        assert GMAIL_SCOPE in response.text
        assert "100 messages per page" in response.text
        assert "Not run during Gmail ingestion" in response.text
        assert "gmail-sync-progress" in response.text
    finally:
        app.dependency_overrides.clear()


def test_disconnected_gmail_page_has_no_sync_progress_dom(db):
    user = _personal_user(db)
    app.dependency_overrides[get_db] = _override_db(db)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        response = TestClient(app).get("/gmail")
        assert response.status_code == 200
        assert "gmail-sync-progress" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_oauth_identity_uses_stable_google_subject_and_does_not_replace_other_connection(db, monkeypatch):
    monkeypatch.setenv("GMAIL_CLIENT_ID", "client-id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GMAIL_REDIRECT_URI", "https://example.test/auth/google/callback")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    other = _personal_user(db, "2")
    db.add(
        GmailCredential(
            user_id=other.id,
            account_email="other@example.test",
            encrypted_token="other-encrypted-token",
            scopes=GMAIL_SCOPE,
        )
    )
    state = "one-time-state"
    _oauth_state(db, state)

    user, credential = complete_oauth(db, state, "authorization-code", client=OAuthClient())

    assert user.google_subject == "stable-google-subject"
    assert user.email == "connected@example.test"
    assert credential.user_id == user.id
    assert credential.scopes == GMAIL_SCOPE
    assert db.scalar(
        select(GmailCredential).where(GmailCredential.user_id == other.id)
    ).account_email == "other@example.test"


def test_oauth_rejects_missing_gmail_readonly_scope(db, monkeypatch):
    monkeypatch.setenv("GMAIL_CLIENT_ID", "client-id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GMAIL_REDIRECT_URI", "https://example.test/auth/google/callback")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    state = "missing-gmail-scope"
    _oauth_state(db, state)

    with pytest.raises(
        GmailSyncError, match="Gmail read-only scope was not granted"
    ):
        complete_oauth(
            db,
            state,
            "authorization-code",
            client=OAuthClient(
                scopes="openid https://www.googleapis.com/auth/userinfo.email"
            ),
        )

    assert db.scalar(select(func.count()).select_from(GmailCredential)) == 0


def test_oauth_rejects_missing_google_identity(db, monkeypatch):
    monkeypatch.setenv("GMAIL_CLIENT_ID", "client-id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GMAIL_REDIRECT_URI", "https://example.test/auth/google/callback")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    state = "missing-google-identity"
    _oauth_state(db, state)

    with pytest.raises(
        GmailSyncError, match="Google identity information is missing or invalid"
    ):
        complete_oauth(
            db,
            state,
            "authorization-code",
            client=OAuthClient(identity={"email": "connected@example.test"}),
        )

    assert db.scalar(select(func.count()).select_from(GmailCredential)) == 0
