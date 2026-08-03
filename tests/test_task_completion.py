from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_execution import approval_token, approve_execution, create_execution, process_next_execution
from app.auth import get_current_user
from app.database import get_db
from app.main import app
from app.models import Analysis, Email, Execution, Task, User


def _setup(db, suffix):
    user = User(
        id=f"82000000-0000-0000-0000-00000000000{suffix}",
        email=f"complete-{suffix}@example.test",
        display_name=f"Complete {suffix}",
    )
    db.add(user)
    db.commit()
    email = Email(
        user_id=user.id,
        external_id=f"complete-{suffix}",
        sender="sender@example.test",
        subject="Action required",
        received_at=datetime.now(UTC).replace(tzinfo=None),
        body="Please complete the supported request by Friday.",
        source="gmail",
        analyzed=True,
    )
    db.add(email)
    db.flush()
    db.add(Analysis(
        user_id=user.id,
        email_id=email.id,
        classification="ACTION_REQUIRED",
        action_required=True,
        summary="Complete the supported request.",
        source="live_gpt",
    ))
    task = Task(user_id=user.id, email_id=email.id, title=f"Complete task {suffix}")
    db.add(task)
    db.commit()
    return user, task


def _client(db, user):
    def override_db():
        yield db
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, base_url="https://testserver")


def test_complete_reopen_are_idempotent_tenant_scoped_and_filter_dashboard(db):
    user, task = _setup(db, "1")
    other, _ = _setup(db, "2")
    assert task.completed_at is None
    try:
        with _client(db, other) as client:
            assert client.post(f"/tasks/{task.id}/complete").status_code == 404
        with _client(db, user) as client:
            first = client.post(f"/tasks/{task.id}/complete", follow_redirects=False)
            assert first.status_code == 303
            db.refresh(task)
            completed_at = task.completed_at
            assert completed_at is not None
            assert task.completed_by == user.id
            client.post(f"/tasks/{task.id}/complete", follow_redirects=False)
            db.refresh(task)
            assert task.completed_at == completed_at
            assert f'/tasks/{task.id}' not in client.get("/dashboard").text
            completed = client.get("/completed")
            assert completed.status_code == 200
            assert task.title in completed.text
            client.post(f"/tasks/{task.id}/reopen", follow_redirects=False)
            db.refresh(task)
            assert task.completed_at is None and task.completed_by is None
            client.post(f"/tasks/{task.id}/reopen", follow_redirects=False)
            assert f'/tasks/{task.id}' in client.get("/dashboard").text
    finally:
        app.dependency_overrides.clear()


def test_execution_and_completion_lifecycles_are_independent_and_history_survives(db):
    user, task = _setup(db, "3")
    execution = create_execution(db, task, "independent-lifecycles")
    approve_execution(db, execution, execution.plan_hash, approval_token(execution))
    process_next_execution(db, agent_runner=lambda *_: None)
    db.refresh(task)
    assert task.completed_at is None
    try:
        with _client(db, user) as client:
            client.post(f"/tasks/{task.id}/complete", follow_redirects=False)
            preserved_result = db.get(Execution, execution.id).result
            assert preserved_result
            client.post(f"/tasks/{task.id}/reopen", follow_redirects=False)
            assert db.get(Execution, execution.id).result == preserved_result
            assert client.get(f"/executions/{execution.id}").status_code == 200
    finally:
        app.dependency_overrides.clear()
