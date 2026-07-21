import json

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.auth import ensure_demo_user, get_current_user
from app.database import get_db
from app.demo_data import DEMO_EMAILS
from app.execution import build_execution_package, parse_structured_result
from app.main import app
from app.models import Analysis, Email, Task, User


VALID_EXECUTORS = {"USER", "ACTIONINBOX", "CHATGPT_WORK", "CODEX", "FUTURE_CONNECTOR", "UNSUPPORTED"}
VALID_READINESS = {"READY_TO_PREPARE", "NEEDS_INFORMATION", "NEEDS_APPROVAL", "INTEGRATION_REQUIRED", "UNSUPPORTED"}


def _client(db, user):
    def override_db():
        yield db
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_fresh_demo_post_ingests_and_triages_automatically_without_get_mutation(db):
    user = ensure_demo_user(db)
    try:
        with _client(db, user) as client:
            assert client.get("/demo").status_code == 405
            assert db.scalar(select(func.count()).select_from(Email)) == 0
            response = client.post("/demo", follow_redirects=False)
            assert response.headers["location"] == "/dashboard?emails_checked=5&tasks_created=3"
            assert db.scalar(select(func.count()).select_from(Email).where(Email.user_id == user.id)) == 5
            assert db.scalar(select(func.count()).select_from(Analysis).where(Analysis.user_id == user.id)) == 5
            assert db.scalar(select(func.count()).select_from(Task).where(Task.user_id == user.id)) == 3
            non_actionable = select(Email.id).where(Email.external_id.in_(["demo-info", "demo-newsletter"]))
            assert db.scalar(select(func.count()).select_from(Task).where(Task.email_id.in_(non_actionable))) == 0
            reopened = client.post("/demo", follow_redirects=False)
            assert reopened.headers["location"] == "/dashboard?emails_checked=0&tasks_created=0"
            assert db.scalar(select(func.count()).select_from(Task).where(Task.user_id == user.id)) == 3
    finally:
        app.dependency_overrides.clear()


def test_automatic_and_manual_triage_remain_tenant_scoped(db):
    demo = ensure_demo_user(db)
    other = User(id="60000000-0000-0000-0000-000000000006", email="isolated@example.test", display_name="Isolated")
    db.add(other); db.commit()
    other_email = Email(**DEMO_EMAILS[0], user_id=other.id, source="test")
    db.add(other_email); db.commit()
    try:
        with _client(db, demo) as client:
            client.post("/demo")
            assert other_email.analyzed is False
            assert db.scalar(select(Analysis).where(Analysis.email_id == other_email.id)) is None
            manual = dict(DEMO_EMAILS[0]); manual["external_id"] = "manual-demo-invoice"
            db.add(Email(**manual, user_id=demo.id, source="test")); db.commit()
            response = client.post("/api/inbox/analyze-all", follow_redirects=False)
            assert response.headers["location"] == "/dashboard?emails_checked=1&tasks_created=1"
            assert db.scalar(select(func.count()).select_from(Task).where(Task.user_id == demo.id)) == 4
            assert other_email.analyzed is False
    finally:
        app.dependency_overrides.clear()


def test_all_actionable_demo_tasks_have_valid_evidence_backed_execution_guidance(db):
    user = ensure_demo_user(db)
    try:
        with _client(db, user) as client:
            client.post("/demo")
            tasks = db.scalars(select(Task).where(Task.user_id == user.id).order_by(Task.id)).all()
            assert len(tasks) == 3
            found_resource_guidance = False
            for task in tasks:
                result = parse_structured_result(task.email.analysis.structured_result)
                guidance = result.execution_guidance
                assert guidance and guidance.ordered_steps and guidance.required_inputs and guidance.safety_checks
                assert guidance.missing_information
                assert guidance.recommended_executor in VALID_EXECUTORS
                assert guidance.readiness in VALID_READINESS
                facts = {fact.id: fact for fact in result.email_facts}
                resource_guidance = {item.id: item for item in result.resource_guidance}
                for item in [guidance.outcome, *guidance.ordered_steps, *guidance.required_inputs, *guidance.safety_checks, guidance.proposed_deliverable]:
                    if item.source == "EMAIL_FACT":
                        assert item.supporting_fact_ids
                        assert all(fact_id in facts for fact_id in item.supporting_fact_ids)
                        assert all(task.email.body[facts[fact_id].evidence.start_offset:facts[fact_id].evidence.end_offset] == facts[fact_id].evidence.exact_quote for fact_id in item.supporting_fact_ids)
                    if item.source == "BUSINESS_GUIDANCE":
                        assert item.supporting_guidance_ids
                        assert all(guidance_id in resource_guidance for guidance_id in item.supporting_guidance_ids)
                    if item.source == "AI_RECOMMENDATION":
                        assert item.source != "EMAIL_FACT"
                for item in result.resource_guidance:
                    found_resource_guidance = True
                    assert item.resource_title
                    assert item.resource_evidence.exact_quote
            assert found_resource_guidance
    finally:
        app.dependency_overrides.clear()


def test_execution_package_is_preview_only_and_tenant_isolated(db):
    user = ensure_demo_user(db)
    other = User(id="70000000-0000-0000-0000-000000000007", email="package-other@example.test", display_name="Other")
    db.add(other); db.commit()
    try:
        with _client(db, user) as client:
            client.post("/demo")
            task = db.scalar(select(Task).where(Task.user_id == user.id).order_by(Task.id))
            analysis_count = db.scalar(select(func.count()).select_from(Analysis))
            preview = client.get(f"/tasks/{task.id}/execution-package?executor=CHATGPT_WORK")
            assert preview.status_code == 200
            assert "Nothing has been executed" in preview.text
            assert "Copy execution brief" in preview.text
            download = client.get(f"/tasks/{task.id}/execution-package/download?executor=CODEX")
            assert download.status_code == 200
            package = download.json()
            assert package["requested_handoff_target"] == "CODEX"
            assert package["approval_required"] is True
            assert "package-other@example.test" not in download.text
            assert db.scalar(select(func.count()).select_from(Analysis)) == analysis_count
        with _client(db, other) as other_client:
            assert other_client.get(f"/tasks/{task.id}/execution-package?executor=CODEX").status_code == 404
            assert other_client.get(f"/tasks/{task.id}/execution-package/download?executor=CODEX").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_package_redacts_secret_shaped_text(db):
    user = ensure_demo_user(db)
    try:
        with _client(db, user) as client:
            client.post("/demo")
            task = db.scalar(select(Task).where(Task.user_id == user.id).order_by(Task.id))
            result = parse_structured_result(task.email.analysis.structured_result)
            result.execution_guidance.executor_explanation = "Use api_key=sk-test-secret-value"
            package = build_execution_package(task, result, "CHATGPT_WORK")
            assert "sk-test-secret-value" not in json.dumps(package)
            assert "[REDACTED]" in json.dumps(package)
    finally:
        app.dependency_overrides.clear()
