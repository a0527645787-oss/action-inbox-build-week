"""Configured table actions. Provider targets and row construction are server owned."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from sqlalchemy import select

from .models import TableDestination, TableAppendRecord, utcnow
from .task_fields import extract_invoice_details
from .execution import parse_structured_result

TABLE_TOOL = "append_row_to_configured_table"
SYSTEM_FIELDS = {"idempotency_key", "created_at", "source_email_id", "task_id", "verification_status"}
FIELD_LABELS = {
    "blank": "Leave empty", "task_title": "Task title", "supplier": "Supplier",
    "invoice_number": "Invoice number", "amount": "Amount", "currency": "Currency",
    "due_date": "Due date", "order_id": "Order ID", "fact:other": "Other verified fact",
    "fact:amount": "Verified amount", "fact:deadline": "Verified deadline",
    "idempotency_key": "ActionInbox tracking key (required once)", "created_at": "Created at",
    "source_email_id": "Source reference (internal)", "task_id": "Task reference (internal)",
    "verification_status": "Verification status", "status": "Tracking status",
}


class TableError(ValueError):
    """Only application-authored, display-safe messages belong here."""


class TableTransientError(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def schema_version(headers, mapping):
    return digest([headers, mapping])


def validate_headers(headers, expected=None):
    if not headers or len(headers) > 100 or any(not isinstance(h, str) or not h.strip() or len(h) > 100 for h in headers) or len(set(headers)) != len(headers):
        raise TableError("Use a single header row with unique, nonempty column names (up to 100 columns).")
    if expected is not None and headers != expected:
        missing = next((h for h in expected if h not in headers), None)
        if missing:
            raise TableError(f"The table is missing the ‘{missing}’ column.")
        raise TableError("The table columns changed. Inspect and save its mapping again before preparing an action.")


def validate_mapping(headers, mapping):
    if len(mapping) != len(headers) or any(field not in FIELD_LABELS for field in mapping):
        raise TableError("Choose a supported field for every table column.")
    if mapping.count("idempotency_key") != 1:
        raise TableError("Map exactly one column to the ActionInbox tracking key to prevent duplicate rows.")


def task_values(task):
    values = {"task_title": task.title, "task_id": str(task.id),
              "source_email_id": task.email.gmail_message_id or task.email.external_id,
              "status": "Approved for tracking", "verification_status": "completed_verified", "blank": ""}
    details = extract_invoice_details(task)
    if details:
        values.update({field: getattr(details, field) for field in ("supplier", "invoice_number", "amount", "currency", "due_date")})
    if task.email.analysis and task.email.analysis.structured_result:
        result = parse_structured_result(task.email.analysis.structured_result)
        facts = {}
        for fact in result.email_facts:
            ev = fact.evidence
            if task.email.body[ev.start_offset:ev.end_offset] != ev.exact_quote:
                continue
            # Generic fields copy accepted evidence; model-normalized values are not free-form writes.
            facts.setdefault("fact:" + fact.type, set()).add(ev.exact_quote)
            match = re.search(r"\border\s*(?:id|number|#)\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9-]*)", ev.exact_quote, re.I)
            if match:
                facts.setdefault("order_id", set()).add(match.group(1))
        values.update({field: next(iter(items)) for field, items in facts.items() if len(items) == 1})
    return values


def destinations_for(db, user_id):
    return db.scalars(select(TableDestination).where(TableDestination.user_id == user_id, TableDestination.enabled.is_(True)).order_by(TableDestination.display_name)).all()


def recommend_destination(task, destinations):
    values = task_values(task)
    matches = [d for d in destinations if all(field in SYSTEM_FIELDS or field == "blank" or values.get(field) for field in json.loads(d.column_mapping))]
    return matches[0].id if len(matches) == 1 else None


def owned_destination(db, user_id, destination_id):
    destination = db.scalar(select(TableDestination).where(TableDestination.id == destination_id, TableDestination.user_id == user_id))
    if destination is None or not destination.enabled or destination.connector_kind != "google_sheets":
        raise TableError("Choose an enabled table from your settings.")
    return destination


def column_letter(number):
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


class GoogleSheetsTable:
    def __init__(self, target, tab, *, read_only=False):
        path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
        if not path or not Path(path).is_file():
            raise TableError("Table access is unavailable. Please contact your administrator.")
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        import httplib2
        from google_auth_httplib2 import AuthorizedHttp
        scope = "https://www.googleapis.com/auth/spreadsheets" + (".readonly" if read_only else "")
        credentials = Credentials.from_service_account_file(path, scopes=[scope])
        self.service = build("sheets", "v4", http=AuthorizedHttp(credentials, http=httplib2.Http(timeout=30)), cache_discovery=False)
        self.target, self.tab = target, tab
        self.read_only = read_only

    def _range(self, cells):
        return "'" + self.tab.replace("'", "''") + "'!" + cells

    def headers(self):
        return (self.service.spreadsheets().values().get(spreadsheetId=self.target, range=self._range("1:1")).execute().get("values") or [[]])[0]

    def find_key(self, key, index, width):
        rows = self.service.spreadsheets().values().get(spreadsheetId=self.target, range=self._range("A:" + column_letter(width))).execute().get("values", [])
        matches = [(n, row + [""] * (width - len(row))) for n, row in enumerate(rows[1:], 2) if len(row) > index and row[index] == key]
        if len(matches) > 1:
            raise TableError("The table contains duplicate tracking keys. Please ask the table owner to review it.")
        return matches[0] if matches else None

    def append_row(self, row):
        if self.read_only:
            raise TableError("This connection only inspects table headers.")
        result = self.service.spreadsheets().values().append(spreadsheetId=self.target,
            range=self._range("A:" + column_letter(len(row))), valueInputOption="RAW",
            insertDataOption="INSERT_ROWS", body={"values": [row]}).execute()
        return int(re.search(r"(\d+)(?::[A-Z]+\d+)?$", result["updates"]["updatedRange"]).group(1))

    def read_row(self, number, width):
        rows = self.service.spreadsheets().values().get(spreadsheetId=self.target,
            range=self._range(f"A{number}:{column_letter(width)}{number}")).execute().get("values") or [[]]
        return rows[0] + [""] * (width - len(rows[0]))


def inspect_headers(target, tab, connector=None):
    try:
        headers = (connector or GoogleSheetsTable(target, tab, read_only=True)).headers()
        validate_headers(headers)
        return headers
    except TableError:
        raise
    except Exception:
        raise TableError("Could not read this table. Check that it is shared with your existing connection and that the tab name is correct.") from None


def action_key(task, destination):
    # Retain the provider tracking identity used by legacy rows on the same target.
    raw = f"{task.user_id}:{task.id}:{destination.target}:{destination.tab_name}"
    return "aip_" + hashlib.sha256(raw.encode()).hexdigest()[:32]


def build_table_plan(task, destination, connector=None):
    headers = json.loads(destination.schema_snapshot)
    mapping = json.loads(destination.column_mapping)
    validate_headers(inspect_headers(destination.target, destination.tab_name, connector), headers)
    validate_mapping(headers, mapping)
    values = task_values(task)
    values.update(idempotency_key=action_key(task, destination), created_at=utcnow().isoformat() + "Z")
    for header, field in zip(headers, mapping):
        if field != "blank" and not values.get(field):
            raise TableError(f"The ‘{header}’ value needs supporting information before you can prepare this action.")
    row = [values[field] for field in mapping]
    return {"version": 3, "task_id": task.id, "action_type": TABLE_TOOL,
        "actions": [{"order": 1, "tool": TABLE_TOOL, "description": "Append the approved row and verify it by read-back."}],
        "destination": {"id": destination.id, "kind": destination.connector_kind, "name": destination.display_name,
                        "target": destination.target, "tab": destination.tab_name},
        "schema": {"headers": headers, "mapping": mapping, "version": schema_version(headers, mapping)},
        "final_row": row, "row_hash": digest(row), "idempotency_key": values["idempotency_key"],
        "task_fingerprint": digest([task.email.body, task.email.analysis.structured_result, task.title]),
        "verification": {"mode": "exact_read_back", "key_column": mapping.index("idempotency_key")},
        "preview": [{"column": h, "value": v} for h, field, v in zip(headers, mapping, row) if field not in SYSTEM_FIELDS and field != "blank"],
        "summary": task.title, "status": "awaiting_approval"}


def validate_destination(db, execution, plan):
    dest = owned_destination(db, execution.user_id, plan["destination"]["id"])
    if (dest.target, dest.tab_name, dest.connector_kind) != (plan["destination"]["target"], plan["destination"]["tab"], plan["destination"]["kind"]) or schema_version(json.loads(dest.schema_snapshot), json.loads(dest.column_mapping)) != plan["schema"]["version"]:
        raise TableError("The table configuration changed. Review its settings before preparing a new action.")
    return dest


def execute_table_plan(db, execution, connector=None):
    plan = json.loads(execution.plan)
    dest = validate_destination(db, execution, plan)
    row, schema = plan["final_row"], plan["schema"]
    if not execution.approved_at or execution.status != "running" or plan["row_hash"] != digest(row) or len(row) != len(schema["headers"]):
        raise TableError("This action does not have a valid approved row.")
    validate_mapping(schema["headers"], schema["mapping"])
    key_index = schema["mapping"].index("idempotency_key")
    if plan["verification"] != {"mode": "exact_read_back", "key_column": key_index} or row[key_index] != plan["idempotency_key"]:
        raise TableError("This action does not have valid verification rules.")
    provider = connector or GoogleSheetsTable(dest.target, dest.tab_name)
    if (provider.target, provider.tab) != (dest.target, dest.tab_name):
        raise TableError("The connection does not match the approved table.")
    try:
        validate_headers(provider.headers(), schema["headers"])
        found = provider.find_key(plan["idempotency_key"], key_index, len(row))
        stored = db.scalar(select(TableAppendRecord).where(TableAppendRecord.action_key == plan["idempotency_key"]))
        if stored and not found:
            raise TableError("A previously verified row is missing. Please review the table; no new row was added.")
        if found:
            number, _ = found
        else:
            # Record the attempt BEFORE the network write. Recovery never blindly repeats an ambiguous append.
            if execution.append_attempted_at:
                raise TableTransientError("append_uncertain")
            execution.append_attempted_at = utcnow()
            db.commit()
            try:
                number = provider.append_row(list(row))
            except Exception:
                found = provider.find_key(plan["idempotency_key"], key_index, len(row))
                if not found:
                    raise TableTransientError("append_uncertain") from None
                number, _ = found
        actual = provider.read_row(number, len(row))
        if actual != row:
            raise TableError("The saved row did not match the approved values. Please review the table before taking further action.")
    except (TableError, TableTransientError):
        raise
    except Exception:
        raise TableTransientError("provider_unavailable") from None
    if not stored:
        db.add(TableAppendRecord(action_key=plan["idempotency_key"], execution_id=execution.id,
            destination_id=dest.id, row_number=number, row_hash=plan["row_hash"]))
    return {"execution_id": execution.id, "message": "Row added and verified.", "destination_name": plan["destination"]["name"],
            "verification_status": "completed_verified", "row_number": number}
