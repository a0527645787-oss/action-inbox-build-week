import hashlib
import hmac
import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.agent_execution import approval_token, approve_execution, create_execution, process_next_execution, serialize_execution
from app.auth import get_current_user, require_personal_user
from app.database import get_db
from app.main import app
from app.models import Execution, TableDestination, TableAppendRecord, utcnow
from app.table_actions import TableError, recommend_destination, task_values
from table_fixtures import _invoice_task


class FakeTable:
    def __init__(self, destination, *, ambiguous=False, mismatch=False, unavailable=False):
        self.target, self.tab = destination.target, destination.tab_name
        self.rows = [json.loads(destination.schema_snapshot)]
        self.append_calls = 0
        self.ambiguous, self.mismatch, self.unavailable = ambiguous, mismatch, unavailable

    def headers(self):
        return self.rows[0]

    def find_key(self, key, index, width):
        if self.unavailable:
            raise TimeoutError("private-provider-response")
        return next(((n, list(r)) for n, r in enumerate(self.rows[1:], 2) if r[index] == key), None)

    def append_row(self, values):
        self.append_calls += 1
        self.rows.append(list(values))
        if self.ambiguous:
            raise TimeoutError("private-provider-response")
        return len(self.rows)

    def read_row(self, number, width):
        return ["wrong"] * width if self.mismatch else list(self.rows[number - 1])


def destination(db, user, name="Expenses", headers=None, mapping=None):
    d = TableDestination(user_id=user.id, display_name=name, target="private-target-" + name,
        tab_name=name, enabled=True, schema_snapshot=json.dumps(headers or ["Supplier", "Invoice", "Tracking"]),
        column_mapping=json.dumps(mapping or ["supplier", "invoice_number", "idempotency_key"]))
    db.add(d); db.commit()
    return d


def prepare(db, task, dest, table):
    return create_execution(db, task, "browser-key", dest.id, connector=table)


def approve(db, execution):
    approve_execution(db, execution, execution.plan_hash, approval_token(execution))


def client_for(db, user):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[require_personal_user] = lambda: user
    token = hmac.new(b"test-session-secret-at-least-thirty-two-bytes", b"action-csrf:", hashlib.sha256).hexdigest()
    return TestClient(app, base_url="https://testserver", headers={"X-CSRF-Token": token})


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_two_schemas_recommendation_and_manual_selection(db):
    user, task = _invoice_task(db)
    first = destination(db, user)
    second = destination(db, user, "Orders", ["Order ID", "Tracking"], ["order_id", "idempotency_key"])
    assert recommend_destination(task, [first, second]) == first.id
    second.column_mapping = json.dumps(["task_title", "idempotency_key"]); db.commit()
    assert recommend_destination(task, [first, second]) is None
    a, b = FakeTable(first), FakeTable(second)
    one, two = prepare(db, task, first, a), prepare(db, task, second, b)
    assert one.id != two.id
    assert json.loads(two.plan)["destination"]["id"] == second.id
    approve(db, one); approve(db, two)
    assert process_next_execution(db, sheets_connector=a).status == "completed_verified"
    assert process_next_execution(db, sheets_connector=b).status == "completed_verified"
    assert len(a.rows[1]) == 3 and len(b.rows[1]) == 2


def test_schema_mismatch_blocks_before_proposal(db):
    user, task = _invoice_task(db)
    d = destination(db, user, headers=["Order ID", "Tracking"], mapping=["task_title", "idempotency_key"])
    table = FakeTable(d); table.rows[0] = ["Order", "Tracking"]
    with pytest.raises(TableError, match="missing the ‘Order ID’"):
        prepare(db, task, d, table)
    assert not db.scalars(select(Execution)).all() and table.append_calls == 0


def test_exact_frozen_row_idempotency_and_one_time_approval(db):
    user, task = _invoice_task(db); d = destination(db, user); table = FakeTable(d)
    ex = prepare(db, task, d, table); frozen = ex.plan
    assert prepare(db, task, d, table).id == ex.id
    assert process_next_execution(db, sheets_connector=table) is None
    approve(db, ex)
    with pytest.raises(ValueError, match="not awaiting"):
        approve(db, ex)
    task.title = "Changed after approval"; db.commit()
    assert process_next_execution(db, sheets_connector=table).status == "completed_verified"
    assert table.rows[1] == json.loads(frozen)["final_row"] and ex.plan == frozen
    assert process_next_execution(db, sheets_connector=table) is None
    assert prepare(db, task, d, table).id == ex.id and table.append_calls == 1
    assert len(db.scalars(select(TableAppendRecord)).all()) == 1


@pytest.mark.parametrize("ambiguous,mismatch,status", [(True, False, "completed_verified"), (False, True, "verification_failed")])
def test_ambiguous_append_and_readback(db, caplog, ambiguous, mismatch, status):
    user, task = _invoice_task(db); d = destination(db, user)
    table = FakeTable(d, ambiguous=ambiguous, mismatch=mismatch)
    ex = prepare(db, task, d, table); approve(db, ex)
    assert process_next_execution(db, sheets_connector=table).status == status
    assert table.append_calls == 1
    assert "private-provider-response" not in caplog.text


def test_bounded_retries_and_expired_lease_recovery(db, caplog):
    user, task = _invoice_task(db); d = destination(db, user); table = FakeTable(d, unavailable=True)
    ex = prepare(db, task, d, table); approve(db, ex)
    for expected in ["queued", "queued", "failed"]:
        assert process_next_execution(db, sheets_connector=table).status == expected
    before = (ex.plan, ex.idempotency_key, ex.status)
    assert prepare(db, task, d, table).id == ex.id
    assert before == (ex.plan, ex.idempotency_key, ex.status) and table.append_calls == 0
    assert "private-provider-response" not in caplog.text


def test_recovery_after_saved_append_never_repeats_write(db):
    user, task = _invoice_task(db); d = destination(db, user); table = FakeTable(d)
    ex = prepare(db, task, d, table); approve(db, ex)
    table.rows.append(json.loads(ex.plan)["final_row"])
    ex.status = "running"; ex.append_attempted_at = utcnow()
    ex.lease_expires_at = utcnow() - timedelta(seconds=1); db.commit()
    assert process_next_execution(db, sheets_connector=table).status == "completed_verified"
    assert table.append_calls == 0


def test_uncertain_append_without_visible_row_never_appends_again(db):
    user, task = _invoice_task(db); d = destination(db, user); table = FakeTable(d)
    ex = prepare(db, task, d, table); approve(db, ex)
    ex.append_attempted_at = utcnow(); db.commit()
    for expected in ["queued", "queued", "failed"]:
        assert process_next_execution(db, sheets_connector=table).status == expected
    assert table.append_calls == 0


def test_tamper_disabled_and_task_change_block_execution(db):
    user, task = _invoice_task(db); d = destination(db, user); table = FakeTable(d)
    ex = prepare(db, task, d, table)
    original_title = task.title; task.title = "Changed"; db.commit()
    with pytest.raises(ValueError, match="data changed"):
        approve(db, ex)
    task.title = original_title; db.commit(); approve(db, ex)
    d.enabled = False; db.commit()
    assert process_next_execution(db, sheets_connector=table).status == "verification_failed"
    assert table.append_calls == 0


def test_tampered_frozen_plan_and_unapproved_queue_fail_closed(db):
    user, task = _invoice_task(db); d = destination(db, user); table = FakeTable(d)
    ex = prepare(db, task, d, table); ex.status = "queued"; db.commit()
    assert process_next_execution(db, sheets_connector=table).status == "failed"
    assert table.append_calls == 0


def test_ui_settings_schema_recheck_csrf_and_private_projection(db, monkeypatch):
    user, task = _invoice_task(db)
    headers = ["Title", "Tracking"]
    monkeypatch.setattr("app.table_routes.inspect_headers", lambda *_: headers)
    with client_for(db, user) as client:
        rejected = client.post("/settings/tables/inspect", headers={"X-CSRF-Token": "invalid"}, data={"name": "Orders", "sheet_url": "https://docs.google.com/spreadsheets/d/private-target-123/edit", "tab": "Orders"})
        assert rejected.status_code == 403
        response = client.post("/settings/tables/inspect", data={"name": "Orders", "sheet_url": "https://docs.google.com/spreadsheets/d/private-target-123/edit", "tab": "Orders"})
        assert response.status_code == 200 and "Map fields for Orders" in response.text
        assert "private-target-123" not in response.text
        d = db.scalar(select(TableDestination))
        assert d.enabled is False
        client.post(f"/settings/tables/{d.id}/save", data={"inspected_schema": json.dumps(headers), "field": ["task_title", "idempotency_key"], "enabled": "true"})
        assert d.enabled is True
        table = FakeTable(d)
        monkeypatch.setattr("app.table_actions.inspect_headers", lambda *_: headers)
        page = client.get(f"/tasks/{task.id}")
        assert "Add to a table" in page.text and "Orders (recommended)" in page.text
        response = client.post(f"/tasks/{task.id}/execution-plan", data={"idempotency_key": "form", "destination_id": d.id})
        assert response.status_code == 200 and "Approve and add row once" in response.text
        ex = db.scalar(select(Execution)); projection = json.dumps(serialize_execution(ex))
        for secret in [d.target, task.email.gmail_message_id, "final_row", "source_email_id"]:
            assert secret not in response.text and secret not in projection
        assert table.append_calls == 0


def test_legacy_history_is_readable_and_untouched(db):
    user, task = _invoice_task(db); d = destination(db, user); table = FakeTable(d)
    ex = prepare(db, task, d, table)
    plan = json.loads(ex.plan)
    ex.tool_name = "append_verified_invoice_row"
    ex.plan = json.dumps({"proposal_id": plan["idempotency_key"], "spreadsheet_id": d.target, "final_row": ["private"]})
    ex.status = "failed"; ex.error_message = "RawSheetsError private"; db.commit()
    before = (ex.plan, ex.plan_hash, ex.idempotency_key, ex.status)
    assert prepare(db, task, d, table).id == ex.id
    assert process_next_execution(db, sheets_connector=table) is None
    with client_for(db, user) as client:
        response = client.get(f"/executions/{ex.id}")
        assert response.status_code == 200 and "failed" in response.text
        assert "RawSheetsError" not in response.text and d.target not in response.text
    assert before == (ex.plan, ex.plan_hash, ex.idempotency_key, ex.status)


def test_exact_invoice_evidence_behavior_is_preserved(db):
    _, task = _invoice_task(db)
    assert task_values(task)["amount"] == "1280"
    data = json.loads(task.email.analysis.structured_result)
    data["email_facts"][0]["evidence"]["exact_quote"] = "Supplier: Invented"
    task.email.analysis.structured_result = json.dumps(data)
    assert not task_values(task).get("supplier")


def test_cross_user_destination_and_wrong_provider_are_blocked(db):
    from app.models import User
    user, task = _invoice_task(db)
    other = User(id="other", email="other@example.test", display_name="Other")
    db.add(other); db.commit()
    private = destination(db, other)
    with pytest.raises(TableError, match="enabled table"):
        prepare(db, task, private, FakeTable(private))
    with client_for(db, user) as client:
        assert client.get(f"/settings/tables/{private.id}").status_code == 404
        assert client.post(f"/settings/tables/{private.id}/disable").status_code == 404
    own = destination(db, user)
    ex = prepare(db, task, own, FakeTable(own)); approve(db, ex)
    wrong = FakeTable(private); wrong.target = "another-target"
    assert process_next_execution(db, sheets_connector=wrong).status == "verification_failed"
    assert wrong.append_calls == 0


def test_frozen_plan_tampering_and_changed_live_headers_never_write(db):
    user, task = _invoice_task(db); d = destination(db, user); table = FakeTable(d)
    ex = prepare(db, task, d, table); approve(db, ex)
    plan = json.loads(ex.plan); plan["final_row"][0] = "Tampered"
    ex.plan = json.dumps(plan); db.commit()
    assert process_next_execution(db, sheets_connector=table).status == "failed"
    assert table.append_calls == 0
    second = destination(db, user, "Second"); table2 = FakeTable(second)
    ex2 = prepare(db, task, second, table2); approve(db, ex2)
    table2.rows[0] = ["Changed", "Invoice", "Tracking"]
    assert process_next_execution(db, sheets_connector=table2).status == "verification_failed"
    assert table2.append_calls == 0


def test_provider_uses_frozen_raw_values_and_escaped_dynamic_range():
    from app.table_actions import GoogleSheetsTable
    from unittest.mock import MagicMock
    provider = GoogleSheetsTable.__new__(GoogleSheetsTable)
    provider.target, provider.tab, provider.read_only = "configured-target", "Owner's Orders", False
    provider.service = MagicMock()
    api = provider.service.spreadsheets.return_value.values.return_value
    api.append.return_value.execute.return_value = {"updates": {"updatedRange": "'Owner''s Orders'!A2:C2"}}
    row = ["=not-a-formula", "tracking", ""]
    assert provider.append_row(row) == 2
    api.append.assert_called_once_with(spreadsheetId="configured-target", range="'Owner''s Orders'!A:C",
        valueInputOption="RAW", insertDataOption="INSERT_ROWS", body={"values": [row]})
    api.get.return_value.execute.return_value = {"values": [["=not-a-formula", "tracking"]]}
    assert provider.read_row(2, 3) == row
    api.get.assert_called_once_with(spreadsheetId="configured-target", range="'Owner''s Orders'!A2:C2")
