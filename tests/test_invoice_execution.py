import json
from datetime import UTC, datetime

import pytest

from app.agent_execution import approval_token, approve_execution, create_execution, process_next_execution
from app.invoice_execution import SHEET_HEADERS, build_invoice_plan, extract_invoice_details
from app.models import Analysis, Email, ExpenseRecord, Task, User


class FakeSheets:
    def __init__(self, *, mismatch=False, fail=False):
        self.rows = [SHEET_HEADERS]
        self.mismatch = mismatch
        self.fail = fail

    def find_invoice(self, supplier, invoice_number):
        for number, row in enumerate(self.rows[1:], 2):
            if row[1] == supplier and row[2] == invoice_number:
                return number
        return None

    def append_row(self, values):
        if self.fail:
            raise TimeoutError("secret-token-must-not-persist")
        self.rows.append(list(values))
        return len(self.rows)

    def read_row(self, row_number):
        row = list(self.rows[row_number - 1])
        if self.mismatch:
            row[3] = "9999"
        return row


def _invoice_task(db, *, include_supplier=True):
    user = User(id="91000000-0000-0000-0000-000000000001", email="invoice@example.test", display_name="Invoice User")
    body = ("Supplier Northstar Office, invoice INV-2048 for USD 1,280 is due by July 21, 2026."
            if include_supplier else "Invoice INV-2048 for USD 1,280 is due by July 21, 2026.")
    db.add(user); db.commit()
    email = Email(user_id=user.id, external_id="invoice-real", sender="billing@example.test", subject="Invoice", received_at=datetime.now(UTC).replace(tzinfo=None), body=body, source="gmail", analyzed=True)
    db.add(email); db.flush()
    facts = []
    quote = body
    if include_supplier:
        facts.append({"id":"supplier","type":"other","value":"Northstar Office","normalized_value":None,"confidence":"high","uncertainty":None,"evidence":{"id":"supplier-e","exact_quote":quote,"start_offset":0,"end_offset":len(quote)}})
    facts += [
        {"id":"amount","type":"amount","value":"USD 1,280","normalized_value":"1280 USD","confidence":"high","uncertainty":None,"evidence":{"id":"amount-e","exact_quote":quote,"start_offset":0,"end_offset":len(quote)}},
        {"id":"due","type":"deadline","value":"July 21, 2026","normalized_value":"2026-07-21","confidence":"high","uncertainty":None,"evidence":{"id":"due-e","exact_quote":quote,"start_offset":0,"end_offset":len(quote)}},
    ]
    structured = {"primary_classification":"invoice","action_required":True,"summary":"Invoice needs tracking.","tasks":[{"id":"t","title":"Track invoice","due_at":None,"due_text":"July 21, 2026","uncertainty":None,"evidence_ids":["amount-e","due-e"]}],"email_facts":facts,"resource_guidance":[],"ai_suggestions":[],"missing_information":[],"execution_guidance":None}
    db.add(Analysis(user_id=user.id,email_id=email.id,classification="invoice",action_required=True,summary="Invoice needs tracking.",structured_result=json.dumps(structured),source="live_gpt"))
    task = Task(user_id=user.id,email_id=email.id,title="Track invoice",deadline_text="July 21, 2026")
    db.add(task); db.commit()
    return task


def test_invoice_extraction_uses_exact_evidence_and_never_invents_missing(db):
    task = _invoice_task(db, include_supplier=False)
    details = extract_invoice_details(task)
    assert details.invoice_number == "INV-2048"
    assert details.amount == "1280" and details.currency == "USD"
    assert details.due_date == "2026-07-21"
    assert details.supplier is None and details.missing_fields == ["supplier"]
    plan = build_invoice_plan(task)
    assert plan["status"] == "needs_information"


def test_no_execution_without_approval_and_changed_data_invalidates_approval(db):
    task = _invoice_task(db)
    execution = create_execution(db, task, "invoice-approval")
    assert execution.status == "awaiting_approval"
    assert process_next_execution(db, sheets_connector=FakeSheets()) is None
    structured = json.loads(task.email.analysis.structured_result)
    structured["email_facts"][2]["normalized_value"] = "2026-07-22"
    task.email.analysis.structured_result = json.dumps(structured)
    db.commit()
    with pytest.raises(ValueError, match="data changed"):
        approve_execution(db, execution, execution.plan_hash, approval_token(execution))


def test_verified_write_duplicate_idempotency_and_readback(db):
    task = _invoice_task(db)
    sheets = FakeSheets()
    execution = create_execution(db, task, "invoice-write")
    approve_execution(db, execution, execution.plan_hash, approval_token(execution))
    result = process_next_execution(db, sheets_connector=sheets)
    assert result.status == "completed_verified"
    assert json.loads(result.result)["verification_status"] == "completed_verified"
    assert db.query(ExpenseRecord).count() == 1
    assert process_next_execution(db, sheets_connector=sheets) is None
    duplicate = create_execution(db, task, "invoice-write-again")
    approve_execution(db, duplicate, duplicate.plan_hash, approval_token(duplicate))
    duplicate = process_next_execution(db, sheets_connector=sheets)
    assert json.loads(duplicate.result)["duplicate"] is True
    assert len(sheets.rows) == 2 and db.query(ExpenseRecord).count() == 1


def test_readback_mismatch_and_api_failure_never_report_success_or_log_secrets(db, caplog):
    task = _invoice_task(db)
    mismatch = create_execution(db, task, "mismatch")
    approve_execution(db, mismatch, mismatch.plan_hash, approval_token(mismatch))
    assert process_next_execution(db, sheets_connector=FakeSheets(mismatch=True)).status == "verification_failed"
    failed = create_execution(db, task, "failure")
    approve_execution(db, failed, failed.plan_hash, approval_token(failed))
    assert process_next_execution(db, sheets_connector=FakeSheets(fail=True)).status == "failed"
    assert "secret-token" not in caplog.text
