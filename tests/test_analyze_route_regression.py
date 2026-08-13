from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.auth import get_current_user
from app.database import get_db
from app.main import app
from app.models import Analysis, Email, Task, User
from app.openai_analysis import LiveAnalysisError, request_live_analysis, validate_evidence
from app.schemas import EmailAnalysisResult


def _client(db, user):
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _gmail_email(db):
    user = User(id="60000000-0000-0000-0000-000000000006", email="analysis@example.test", display_name="Analysis")
    db.add(user)
    db.commit()
    body = (
        "Supplier: Northstar Office Supplies\n"
        "Invoice number: AI-TEST-001\n"
        "Amount: USD 184.50\n"
        "Due date: August 20, 2026\n"
        "Requested action: add the expense to the company expense tracker"
    )
    email = Email(
        user_id=user.id,
        external_id="gmail-regression-message",
        gmail_message_id="gmail-regression-message",
        sender="billing@example.test",
        subject="Invoice AI-TEST-001 requires approval",
        received_at=datetime.now(UTC).replace(tzinfo=None),
        body=body,
        source="gmail",
        analyzed=False,
    )
    db.add(email)
    db.commit()
    return user, email


def _successful_result(body):
    def evidence(identifier, quote):
        start = body.index(quote)
        return {"id": identifier, "exact_quote": quote, "start_offset": start, "end_offset": start + len(quote)}

    raw = {
        "primary_classification": "invoice",
        "action_required": True,
        "summary": "The invoice requires an expense-tracker entry.",
        "tasks": [{"id": "task-1", "title": "Add invoice AI-TEST-001 to the company expense tracker", "due_at": "2026-08-20T00:00:00", "due_text": "August 20, 2026", "uncertainty": None, "evidence_ids": ["evidence-action", "evidence-due"]}],
        "email_facts": [
            {"id": "fact-action", "type": "other", "value": "add the expense to the company expense tracker", "normalized_value": None, "confidence": "high", "uncertainty": None, "evidence": evidence("evidence-action", "add the expense to the company expense tracker")},
            {"id": "fact-due", "type": "deadline", "value": "August 20, 2026", "normalized_value": "2026-08-20", "confidence": "high", "uncertainty": None, "evidence": evidence("evidence-due", "August 20, 2026")},
        ],
        "resource_guidance": [],
        "ai_suggestions": [],
        "missing_information": [],
        "execution_guidance": None,
    }
    return EmailAnalysisResult.model_validate(raw)


def _aws_result(body, *, action_required, tasks):
    quote = "CloudTrail consumers may depend on the replaced Billing event names or sources."
    start = body.index(quote)
    evidence = {
        "id": "evidence-cloudtrail",
        "exact_quote": quote,
        "start_offset": start,
        "end_offset": start + len(quote),
    }
    return EmailAnalysisResult.model_validate({
        "primary_classification": "action_required",
        "action_required": action_required,
        "summary": "A scheduled AWS Billing API migration may affect conditional CloudTrail consumers.",
        "tasks": tasks,
        "email_facts": [{
            "id": "fact-cloudtrail",
            "type": "other",
            "value": "CloudTrail consumers may depend on replaced event names or sources",
            "normalized_value": None,
            "confidence": "medium",
            "uncertainty": "Whether the user has affected parsing, alerts, or automation is unknown.",
            "evidence": evidence,
        }],
        "resource_guidance": [],
        "ai_suggestions": [],
        "missing_information": ["Whether any CloudTrail parsing, alerts, or automation depend on these events."],
        "execution_guidance": None,
    })


def _aws_task():
    return {
        "id": "task-aws-check",
        "title": (
            "Check whether CloudTrail parsing, alerts, or automation depend on the replaced AWS Billing "
            "event names or sources; if they do, update them before the scheduled migration."
        ),
        "due_at": None,
        "due_text": None,
        "uncertainty": "Only update affected software if such dependencies exist.",
        "evidence_ids": ["evidence-cloudtrail"],
    }


def test_aws_semantic_inconsistencies_are_rejected():
    body = "CloudTrail consumers may depend on the replaced Billing event names or sources."
    cases = [
        _aws_result(body, action_required=False, tasks=[]),
        _aws_result(body, action_required=True, tasks=[]),
        _aws_result(body, action_required=False, tasks=[_aws_task()]),
    ]
    for result in cases:
        try:
            validate_evidence(result, body)
        except LiveAnalysisError as exc:
            assert str(exc) == "Structured analysis failed local validation"
        else:
            raise AssertionError("Inconsistent structured analysis was accepted")


def test_valid_conditional_aws_task_passes_semantic_validation():
    body = "CloudTrail consumers may depend on the replaced Billing event names or sources."
    result = validate_evidence(_aws_result(body, action_required=True, tasks=[_aws_task()]), body)
    assert result.action_required is True
    assert [task.title for task in result.tasks] == [_aws_task()["title"]]
    assert "if they do" in result.tasks[0].title
    assert result.tasks[0].uncertainty


class _SequenceResponses:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return type("Response", (), {"output_parsed": self.outputs.pop(0)})()


class _SequenceClient:
    def __init__(self, outputs):
        self.responses = _SequenceResponses(outputs)


def test_invalid_then_valid_repair_persists_exactly_one_pair(db):
    user, email = _gmail_email(db)
    body = "CloudTrail consumers may depend on the replaced Billing event names or sources."
    email.body = body
    db.commit()
    invalid = _aws_result(body, action_required=True, tasks=[])
    valid = _aws_result(body, action_required=True, tasks=[_aws_task()])
    client = _SequenceClient([invalid, valid])

    analysis = __import__("app.analysis", fromlist=["analyze_email"]).analyze_email(db, email, client=client)

    assert len(client.responses.calls) == 2
    assert analysis.action_required is True
    assert db.scalar(select(func.count()).select_from(Analysis).where(Analysis.email_id == email.id)) == 1
    assert db.scalar(select(func.count()).select_from(Task).where(Task.email_id == email.id)) == 1


def test_failed_repair_reanalysis_restores_previous_analysis(db, monkeypatch):
    user, email = _gmail_email(db)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-secret")
    original = _successful_result(email.body)
    monkeypatch.setattr("app.analysis.request_live_analysis", lambda *args, **kwargs: original)
    from app.analysis import analyze_email
    previous = analyze_email(db, email)
    previous_task = db.scalar(select(Task).where(Task.email_id == email.id))
    previous_analysis_id = previous.id
    previous_task_id = previous_task.id

    body = "CloudTrail consumers may depend on the replaced Billing event names or sources."
    email.body = body
    db.commit()
    invalid = _aws_result(body, action_required=True, tasks=[])
    repair_client = _SequenceClient([invalid, invalid])

    def bounded_failure(target, **kwargs):
        return request_live_analysis(target, client=repair_client)

    monkeypatch.setattr("app.analysis.request_live_analysis", bounded_failure)
    try:
        with _client(db, user) as http:
            response = http.post(f"/api/emails/{email.id}/reanalyze", follow_redirects=False)
            assert response.status_code == 303
            assert response.headers["location"] == f"/emails/{email.id}?analysis_error=1"
            assert "could not be analyzed safely" in http.get(response.headers["location"]).text
    finally:
        app.dependency_overrides.clear()

    assert len(repair_client.responses.calls) == 2
    db.expire_all()
    assert db.get(Analysis, previous_analysis_id) is not None
    assert db.get(Task, previous_task_id) is not None
    assert db.scalar(select(func.count()).select_from(Analysis).where(Analysis.email_id == email.id)) == 1
    assert db.scalar(select(func.count()).select_from(Task).where(Task.email_id == email.id)) == 1


def test_failed_analysis_keeps_gmail_message_and_returns_safe_error(db, monkeypatch):
    user, email = _gmail_email(db)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-secret")

    def fail(*args, **kwargs):
        raise RuntimeError("simulated provider failure with token secret-sensitive-value")

    monkeypatch.setattr("app.analysis.request_live_analysis", fail)
    try:
        with _client(db, user) as client:
            response = client.post(f"/api/emails/{email.id}/analyze", follow_redirects=False)
            assert response.status_code == 303
            assert response.headers["location"] == f"/emails/{email.id}?analysis_error=1"
            page = client.get(response.headers["location"])
            assert page.status_code == 200
            assert "could not be analyzed safely" in page.text
            assert "simulated provider failure" not in page.text
            assert "secret-sensitive-value" not in page.text
    finally:
        app.dependency_overrides.clear()

    stored = db.get(Email, email.id)
    assert stored is not None and stored.analyzed is False
    assert db.scalar(select(func.count()).select_from(Analysis).where(Analysis.email_id == email.id)) == 0
    assert db.scalar(select(func.count()).select_from(Task).where(Task.email_id == email.id)) == 0


def test_rejected_semantic_analysis_remains_retryable(db, monkeypatch):
    user, email = _gmail_email(db)
    body = "CloudTrail consumers may depend on the replaced Billing event names or sources."
    email.body = body
    db.commit()
    inconsistent = _aws_result(body, action_required=False, tasks=[])
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-secret")

    def reject(*args, **kwargs):
        return validate_evidence(inconsistent, body)

    monkeypatch.setattr("app.analysis.request_live_analysis", reject)
    try:
        with _client(db, user) as client:
            response = client.post(f"/api/emails/{email.id}/analyze", follow_redirects=False)
            assert response.status_code == 303
            assert response.headers["location"] == f"/emails/{email.id}?analysis_error=1"
            page = client.get(response.headers["location"])
            assert "could not be analyzed safely" in page.text
            assert "semantically inconsistent" not in page.text
    finally:
        app.dependency_overrides.clear()

    db.refresh(email)
    assert email.analyzed is False
    assert db.scalar(select(func.count()).select_from(Analysis).where(Analysis.email_id == email.id)) == 0
    assert db.scalar(select(func.count()).select_from(Task).where(Task.email_id == email.id)) == 0


def test_success_creates_one_visible_task_and_retry_is_idempotent(db, monkeypatch):
    user, email = _gmail_email(db)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-secret")
    result = _successful_result(email.body)
    monkeypatch.setattr("app.analysis.request_live_analysis", lambda *args, **kwargs: result)

    try:
        with _client(db, user) as client:
            first = client.post(f"/api/emails/{email.id}/analyze", follow_redirects=False)
            assert first.status_code == 303
            task = db.scalar(select(Task).where(Task.email_id == email.id))
            assert task is not None
            assert first.headers["location"] == f"/tasks/{task.id}"
            assert db.scalar(select(func.count()).select_from(Task).where(Task.email_id == email.id)) == 1
            assert task.title in client.get("/dashboard").text

            retry = client.post(f"/api/emails/{email.id}/analyze", follow_redirects=False)
            assert retry.status_code == 303
            assert retry.headers["location"] == f"/tasks/{task.id}"
            assert db.scalar(select(func.count()).select_from(Task).where(Task.email_id == email.id)) == 1
    finally:
        app.dependency_overrides.clear()


def test_valid_action_required_analysis_creates_one_visible_active_task(db, monkeypatch):
    user, email = _gmail_email(db)
    body = "CloudTrail consumers may depend on the replaced Billing event names or sources."
    email.body = body
    db.commit()
    result = validate_evidence(_aws_result(body, action_required=True, tasks=[_aws_task()]), body)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-secret")
    monkeypatch.setattr("app.analysis.request_live_analysis", lambda *args, **kwargs: result)

    try:
        with _client(db, user) as client:
            first = client.post(f"/api/emails/{email.id}/analyze", follow_redirects=False)
            task = db.scalar(select(Task).where(Task.email_id == email.id))
            assert first.status_code == 303
            assert task is not None and task.completed_at is None
            assert task.title == _aws_task()["title"]
            assert db.scalar(select(func.count()).select_from(Analysis).where(Analysis.email_id == email.id)) == 1
            assert db.scalar(select(func.count()).select_from(Task).where(Task.email_id == email.id)) == 1
            assert task.title in client.get("/dashboard").text

            retry = client.post(f"/api/emails/{email.id}/analyze", follow_redirects=False)
            assert retry.status_code == 303
            assert db.scalar(select(func.count()).select_from(Analysis).where(Analysis.email_id == email.id)) == 1
            assert db.scalar(select(func.count()).select_from(Task).where(Task.email_id == email.id)) == 1
    finally:
        app.dependency_overrides.clear()


def test_inconsistent_legacy_analysis_does_not_show_action_required_badge(db):
    user, email = _gmail_email(db)
    email.analyzed = True
    db.add(Analysis(
        email=email,
        user_id=user.id,
        classification="action_required",
        action_required=False,
        summary="Synthetic inconsistent legacy analysis.",
        structured_result=None,
        source="live_gpt",
    ))
    db.commit()

    try:
        with _client(db, user) as client:
            page = client.get(f"/emails/{email.id}")
            assert page.status_code == 200
            assert "ACTION REQUIRED" not in page.text
            assert "Open task" not in page.text
    finally:
        app.dependency_overrides.clear()


def test_form_script_restores_working_button_on_pageshow():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "window.addEventListener('pageshow', resetSubmittedForms)" in source
    assert "button.disabled = false" in source
    assert "button.innerHTML = button.dataset.originalHtml" in source
    assert "event.preventDefault()" in source
    assert "form.dataset.submitting === 'true'" in source
