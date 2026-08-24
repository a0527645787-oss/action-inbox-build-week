import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent_execution import approval_token, approve_execution, create_execution, process_next_execution
from app.auth import get_current_user
from app.database import get_db
from app.invoice_execution import SHEET_HEADERS, SheetsNotConfigured, build_invoice_plan, extract_invoice_details
from app.main import app
from app.models import Analysis, Email, Execution, SheetAppendRecord, Task, User, utcnow


class FakeSheets:
    sheet_id = "sheet-target-123"
    tab = "Expenses"

    def __init__(self, *, ambiguous=False, mismatch=False):
        self.rows = [SHEET_HEADERS]
        self.append_calls = 0
        self.ambiguous = ambiguous
        self.mismatch = mismatch

    def find_proposal(self, proposal_id):
        for number, row in enumerate(self.rows[1:], 2):
            if row[1] == proposal_id:
                return number, list(row)
        return None

    def append_row(self, values):
        self.append_calls += 1
        self.rows.append(list(values))
        if self.ambiguous:
            raise TimeoutError("provider-secret-must-not-escape")
        number = len(self.rows)
        return number, f"'Expenses'!A{number}:K{number}"

    def read_row(self, row_number):
        row = list(self.rows[row_number - 1])
        if self.mismatch:
            row[4] = "999999"
        return row


def _invoice_task(db):
    user = User(id="91000000-0000-0000-0000-000000000001", email="invoice@example.test", display_name="Invoice User")
    db.add(user)
    db.commit()
    quotes = [
        "Supplier: Northstar Office",
        "Invoice Number: INV-2048",
        "Amount: 1,280",
        "Currency: USD",
        "Due Date: July 21, 2026",
    ]
    body = "\n".join(quotes)
    email = Email(
        user_id=user.id,
        external_id="invoice-v2",
        gmail_message_id="synthetic-gmail-invoice",
        sender="billing@example.test",
        subject="Synthetic invoice",
        received_at=datetime.now(UTC).replace(tzinfo=None),
        body=body,
        source="gmail",
        analyzed=True,
    )
    db.add(email)
    db.flush()
    def evidence(index):
        quote = quotes[index]
        start = body.index(quote)
        return {"id": f"server-evidence-{index}", "exact_quote": quote, "start_offset": start, "end_offset": start + len(quote)}

    facts = [
        {"id": "server-fact-0", "type": "other", "value": "Northstar Office", "normalized_value": None, "confidence": "high", "uncertainty": None, "evidence": evidence(0)},
        {"id": "server-fact-1", "type": "other", "value": "INV-2048", "normalized_value": None, "confidence": "high", "uncertainty": None, "evidence": evidence(1)},
        {"id": "server-fact-2", "type": "amount", "value": "1,280", "normalized_value": "1280", "confidence": "high", "uncertainty": None, "evidence": evidence(2)},
        {"id": "server-fact-3", "type": "other", "value": "USD", "normalized_value": "USD", "confidence": "high", "uncertainty": None, "evidence": evidence(3)},
        {"id": "server-fact-4", "type": "deadline", "value": "July 21, 2026", "normalized_value": "2026-07-21", "confidence": "high", "uncertainty": None, "evidence": evidence(4)},
    ]
    structured = {
        "schema_version": "2",
        "primary_classification": "invoice",
        "action_required": True,
        "summary": "The invoice requires an approval-gated tracking row.",
        "tasks": [{"id": "server-task-0", "title": "Track invoice INV-2048", "due_at": None, "due_text": "July 21, 2026", "uncertainty": None, "evidence_ids": [fact["id"] for fact in facts]}],
        "email_facts": facts,
        "resource_guidance": [],
        "ai_suggestions": [],
        "missing_information": [],
        "execution_guidance": None,
    }
    db.add(Analysis(
        user_id=user.id,
        email_id=email.id,
        classification="invoice",
        action_required=True,
        summary=structured["summary"],
        structured_result=json.dumps(structured),
        source="live_gpt",
    ))
    task = Task(user_id=user.id, email_id=email.id, title="Track invoice INV-2048", deadline_text="July 21, 2026")
    db.add(task)
    db.commit()
    return user, task


def _configure(monkeypatch):
    monkeypatch.setenv("ACTIONINBOX_SHEETS_ENABLED", "true")
    monkeypatch.setenv("ACTIONINBOX_SHEET_ID", "sheet-target-123")
    monkeypatch.setenv("ACTIONINBOX_SHEET_TAB", "Expenses")


def test_v2_evidence_builds_one_frozen_complete_proposal_without_writing(db, monkeypatch):
    _configure(monkeypatch)
    _, task = _invoice_task(db)
    details = extract_invoice_details(task)
    assert (details.supplier, details.invoice_number, details.amount, details.currency, details.due_date) == (
        "Northstar Office", "INV-2048", "1280", "USD", "2026-07-21",
    )
    execution = create_execution(db, task, "browser-random-one")
    duplicate = create_execution(db, task, "browser-random-two")
    assert duplicate.id == execution.id
    assert execution.status == "awaiting_approval"
    plan = json.loads(execution.plan)
    assert plan["spreadsheet_id"] == "sheet-target-123" and plan["sheet_tab"] == "Expenses"
    assert plan["ordered_columns"] == SHEET_HEADERS
    assert [item["value"] for item in plan["column_mapping"]] == plan["final_row"]
    assert plan["final_row"][0] == plan["invoice"]["created_at"]
    assert plan["final_row"][1] == plan["proposal_id"]
    assert process_next_execution(db, sheets_connector=pytest.fail) is None
    assert db.scalar(select(func.count()).select_from(SheetAppendRecord)) == 0


def test_exact_sheet_tab_whitespace_is_preserved_in_frozen_proposal(db, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setenv("ACTIONINBOX_SHEET_TAB", "Expenses ")
    _, task = _invoice_task(db)
    execution = create_execution(db, task, "exact-tab")
    assert json.loads(execution.plan)["sheet_tab"] == "Expenses "
    assert execution.status == "awaiting_approval"
    assert db.scalar(select(func.count()).select_from(SheetAppendRecord)) == 0


def test_separate_approval_appends_raw_row_once_and_persists_exact_receipt(db, monkeypatch):
    _configure(monkeypatch)
    _, task = _invoice_task(db)
    sheets = FakeSheets()
    execution = create_execution(db, task, "first")
    approve_execution(db, execution, execution.plan_hash, approval_token(execution))
    processed = process_next_execution(db, sheets_connector=sheets)
    receipt = json.loads(processed.result)
    assert processed.status == "completed_verified" and sheets.append_calls == 1
    assert sheets.rows[1] == json.loads(execution.plan)["final_row"]
    assert receipt == {
        "execution_id": execution.id,
        "message": "Approved invoice row appended exactly once and verified by read-back.",
        "plan_hash": execution.plan_hash,
        "proposal_id": json.loads(execution.plan)["proposal_id"],
        "row_number": 2,
        "sheet_range": "'Expenses'!A2:K2",
        "sheet_tab": "Expenses",
        "spreadsheet_id": "sheet-target-123",
        "task_id": task.id,
        "tool_name": "append_verified_invoice_row",
        "verification_status": "completed_verified",
    }
    assert db.scalar(select(func.count()).select_from(SheetAppendRecord)) == 1
    assert process_next_execution(db, sheets_connector=sheets) is None
    assert sheets.append_calls == 1


def test_ambiguous_append_searches_stable_proposal_and_does_not_duplicate(db, monkeypatch, caplog):
    _configure(monkeypatch)
    _, task = _invoice_task(db)
    sheets = FakeSheets(ambiguous=True)
    execution = create_execution(db, task, "ambiguous")
    approve_execution(db, execution, execution.plan_hash, approval_token(execution))
    processed = process_next_execution(db, sheets_connector=sheets)
    assert processed.status == "completed_verified"
    assert sheets.append_calls == 1 and len(sheets.rows) == 2
    assert "provider-secret" not in caplog.text


def test_invalid_readback_never_reports_success(db, monkeypatch):
    _configure(monkeypatch)
    _, task = _invoice_task(db)
    execution = create_execution(db, task, "mismatch")
    approve_execution(db, execution, execution.plan_hash, approval_token(execution))
    processed = process_next_execution(db, sheets_connector=FakeSheets(mismatch=True))
    assert processed.status == "verification_failed" and processed.result is None
    assert db.scalar(select(func.count()).select_from(SheetAppendRecord)) == 0


def test_provider_lookup_retries_are_bounded_and_content_free(db, monkeypatch, caplog):
    class UnavailableSheets(FakeSheets):
        def find_proposal(self, proposal_id):
            raise TimeoutError("private-provider-response")

    _configure(monkeypatch)
    _, task = _invoice_task(db)
    execution = create_execution(db, task, "bounded")
    approve_execution(db, execution, execution.plan_hash, approval_token(execution))
    sheets = UnavailableSheets()
    assert process_next_execution(db, sheets_connector=sheets).status == "queued"
    assert process_next_execution(db, sheets_connector=sheets).status == "queued"
    final = process_next_execution(db, sheets_connector=sheets)
    assert final.status == "failed" and final.attempt_count == 3
    assert sheets.append_calls == 0
    assert "private-provider-response" not in caplog.text


def test_expired_worker_lease_recovers_without_rebuilding_approved_row(db, monkeypatch):
    _configure(monkeypatch)
    _, task = _invoice_task(db)
    execution = create_execution(db, task, "lease")
    frozen_plan = execution.plan
    approve_execution(db, execution, execution.plan_hash, approval_token(execution))
    execution.status = "running"
    execution.started_at = utcnow() - timedelta(minutes=10)
    execution.lease_expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    processed = process_next_execution(db, sheets_connector=FakeSheets())
    assert processed.status == "completed_verified"
    assert processed.plan == frozen_plan and processed.attempt_count == 1


def test_unconfigured_sheets_fails_closed_before_proposal(db, monkeypatch):
    monkeypatch.setenv("ACTIONINBOX_SHEETS_ENABLED", "false")
    monkeypatch.delenv("ACTIONINBOX_SHEET_ID", raising=False)
    monkeypatch.delenv("ACTIONINBOX_SHEET_TAB", raising=False)
    _, task = _invoice_task(db)
    with pytest.raises(SheetsNotConfigured, match="SHEETS_NOT_CONFIGURED"):
        build_invoice_plan(task)
    assert db.scalar(select(func.count()).select_from(Execution)) == 0


def test_connected_task_page_offers_prepare_and_proposal_preview_is_exact(db, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-at-least-thirty-two-bytes")
    user, task = _invoice_task(db)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        client = TestClient(app, base_url="https://testserver")
        task_page = client.get(f"/tasks/{task.id}")
        assert task_page.status_code == 200 and "Prepare expense record" in task_page.text
        assert "Prepare expense record</button>" in task_page.text
        prepared = client.post(f"/tasks/{task.id}/execution-plan", data={"idempotency_key": "page-submit"}, follow_redirects=False)
        assert prepared.status_code == 303
        duplicate = client.post(f"/tasks/{task.id}/execution-plan", data={"idempotency_key": "duplicate-click"}, follow_redirects=False)
        assert duplicate.status_code == 303 and duplicate.headers["location"] == prepared.headers["location"]
        preview = client.get(prepared.headers["location"])
        assert preview.status_code == 200
        for expected in ("sheet-target-123", "Expenses", "Proposal ID", "Complete final row", "Northstar Office", "INV-2048", "Confirm and queue this exact row"):
            assert expected in preview.text
        execution = db.scalar(select(Execution).where(Execution.task_id == task.id))
        assert execution.status == "awaiting_approval"
        assert db.scalar(select(func.count()).select_from(Execution)) == 1
        assert db.scalar(select(func.count()).select_from(SheetAppendRecord)) == 0
    finally:
        app.dependency_overrides.clear()


def test_proposal_failure_redirects_to_safe_notice_without_rows(db, monkeypatch):
    _configure(monkeypatch)
    user, task = _invoice_task(db)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr("app.main.create_execution", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("private details")))
    try:
        client = TestClient(app, base_url="https://testserver")
        response = client.post(
            f"/tasks/{task.id}/execution-plan",
            data={"idempotency_key": "safe-failure"},
            follow_redirects=False,
        )
        assert response.status_code == 303 and response.headers["location"].endswith("?proposal_error=1")
        notice = client.get(response.headers["location"])
        assert notice.status_code == 200
        assert "could not be prepared safely" in notice.text
        assert "private details" not in notice.text
        assert db.scalar(select(func.count()).select_from(Execution)) == 0
        assert db.scalar(select(func.count()).select_from(SheetAppendRecord)) == 0
        assert "Prepare expense record</button>" in notice.text
    finally:
        app.dependency_overrides.clear()


def test_production_compose_keeps_credentials_worker_only_and_optional():
    compose = Path("deploy/docker-compose.production.yml").read_text(encoding="utf-8")
    app_section, worker_and_after = compose.split("  execution-worker:", 1)
    worker_section = worker_and_after.split("  gmail-sync-worker:", 1)[0]
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in app_section
    assert "GOOGLE_APPLICATION_CREDENTIALS: /run/secrets/actioninbox-google-service-account.json" in worker_section
    assert "${GOOGLE_APPLICATION_CREDENTIALS:-/dev/null}" in worker_section
    assert "${GOOGLE_APPLICATION_CREDENTIALS:?" not in compose
