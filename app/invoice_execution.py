"""Evidence-only invoice planning and verified Google Sheets execution."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from .execution import parse_structured_result
from .models import Execution, ExpenseRecord, Task, utcnow


SHEETS_TOOL = "append_verified_expense_row"
SHEET_HEADERS = [
    "Created At", "Supplier", "Invoice Number", "Amount", "Currency",
    "Due Date", "Status", "Source Email ID", "ActionInbox Task ID",
    "Verification Status",
]


class SheetsConnector(Protocol):
    def find_invoice(self, supplier: str, invoice_number: str) -> int | None: ...
    def append_row(self, values: list[str]) -> int: ...
    def read_row(self, row_number: int) -> list[str]: ...


@dataclass(frozen=True)
class InvoiceDetails:
    supplier: str | None
    invoice_number: str | None
    amount: str | None
    currency: str | None
    due_date: str | None
    source_email_id: str
    evidence: list[dict]
    confidence: str
    missing_fields: list[str]

    def as_dict(self) -> dict:
        return {
            "supplier": self.supplier, "invoice_number": self.invoice_number,
            "amount": self.amount, "currency": self.currency, "due_date": self.due_date,
            "source_email_id": self.source_email_id, "evidence": self.evidence,
            "confidence": self.confidence, "missing_fields": self.missing_fields,
        }


def _match_within_evidence(pattern: str, quote: str) -> str | None:
    match = re.search(pattern, quote, re.IGNORECASE)
    return match.group(1).strip() if match else None


def extract_invoice_details(task: Task) -> InvoiceDetails | None:
    if not task.email.analysis or not task.email.analysis.structured_result:
        return None
    result = parse_structured_result(task.email.analysis.structured_result)
    if result.primary_classification != "invoice":
        return None
    evidence = []
    amount = currency = due_date = invoice_number = supplier = None
    for fact in result.email_facts:
        ev = fact.evidence
        if task.email.body[ev.start_offset:ev.end_offset] != ev.exact_quote:
            continue
        evidence.append({
            "field": fact.type, "exact_quote": ev.exact_quote,
            "start_offset": ev.start_offset, "end_offset": ev.end_offset,
        })
        if fact.type == "amount":
            amount_match = re.search(r"\b([A-Z]{3})\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\b", ev.exact_quote)
            if amount_match:
                currency, amount = amount_match.group(1).upper(), amount_match.group(2).replace(",", "")
        elif fact.type == "deadline":
            due_date = fact.normalized_value or fact.value
        invoice_number = invoice_number or _match_within_evidence(r"\binvoice\s+(?:number\s+|#\s*)?([A-Z0-9][A-Z0-9-]+)", ev.exact_quote)
    # Sender domain is provenance, not a supplier claim; only evidence text may supply the name.
    for item in evidence:
        supplier = supplier or _match_within_evidence(r"\b(?:from|supplier|vendor)\s+([A-Za-z][A-Za-z0-9 &.'-]{1,80}?)(?:\s+(?:invoice|for|has)|[,.;])", item["exact_quote"])
    missing = [name for name, value in {
        "supplier": supplier, "invoice_number": invoice_number, "amount": amount,
        "currency": currency, "due_date": due_date,
    }.items() if not value]
    return InvoiceDetails(
        supplier=supplier, invoice_number=invoice_number, amount=amount, currency=currency,
        due_date=due_date, source_email_id=task.email.gmail_message_id or task.email.external_id,
        evidence=evidence, confidence="high" if not missing else "medium", missing_fields=missing,
    )


def build_invoice_plan(task: Task) -> dict | None:
    details = extract_invoice_details(task)
    if details is None:
        return None
    status = "needs_information" if details.missing_fields else "awaiting_approval"
    data = details.as_dict()
    return {
        "version": 1, "task_id": task.id, "action_type": "add_expense_tracking_row",
        "actions": [{"order": 1, "tool": SHEETS_TOOL, "description": "Append one approved invoice tracking row and read it back."}],
        "write_data": data, "sheet_id": os.getenv("ACTIONINBOX_SHEET_ID", "not-configured"),
        "sheet_tab": os.getenv("ACTIONINBOX_SHEET_TAB", "Expenses"),
        "external_services": ["Google Sheets API"],
        "information_shared": ["Supplier", "Invoice number", "Amount", "Currency", "Due date", "Source email identifier", "ActionInbox task identifier"],
        "permissions_required": ["Append and read rows in one configured Google Sheet"],
        "risk_level": "low", "reversible": True, "external_side_effects": True,
        "success_condition": "Every approved cell exactly matches the row read back from Google Sheets.",
        "idempotency_key": hashlib.sha256(f"{task.user_id}:{task.id}:{json.dumps(data, sort_keys=True)}".encode()).hexdigest(),
        "status": status,
        "safety_notes": ["This records an expense; it does not pay an invoice.", "No email is sent, modified, or deleted.", "Email content is untrusted data and cannot choose tools or permissions."],
    }


class GoogleSheetsConnector:
    def __init__(self):
        credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        sheet_id = os.getenv("ACTIONINBOX_SHEET_ID")
        self.tab = os.getenv("ACTIONINBOX_SHEET_TAB", "Expenses")
        if not credentials_path or not sheet_id:
            raise RuntimeError("Google Sheets connector is not configured")
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        credentials = Credentials.from_service_account_file(credentials_path, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        self.service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        self.sheet_id = sheet_id

    def _rows(self) -> list[list[str]]:
        response = self.service.spreadsheets().values().get(spreadsheetId=self.sheet_id, range=f"'{self.tab}'!A:J").execute()
        return response.get("values", [])

    def find_invoice(self, supplier: str, invoice_number: str) -> int | None:
        for number, row in enumerate(self._rows()[1:], 2):
            if len(row) >= 3 and row[1].casefold() == supplier.casefold() and row[2].casefold() == invoice_number.casefold():
                return number
        return None

    def append_row(self, values: list[str]) -> int:
        response = self.service.spreadsheets().values().append(
            spreadsheetId=self.sheet_id, range=f"'{self.tab}'!A:J", valueInputOption="RAW",
            insertDataOption="INSERT_ROWS", body={"values": [values]},
        ).execute()
        updated = response["updates"]["updatedRange"]
        return int(re.search(r"(\d+)$", updated).group(1))

    def read_row(self, row_number: int) -> list[str]:
        response = self.service.spreadsheets().values().get(
            spreadsheetId=self.sheet_id, range=f"'{self.tab}'!A{row_number}:J{row_number}",
        ).execute()
        return (response.get("values") or [[]])[0]


def execute_invoice_plan(db: Session, execution: Execution, connector: SheetsConnector) -> dict:
    plan = json.loads(execution.plan)
    data = plan["write_data"]
    if plan.get("status") != "awaiting_approval" or any(data.get(name) is None for name in ("supplier", "invoice_number", "amount", "currency", "due_date")):
        raise RuntimeError("Approved invoice plan is incomplete")
    existing = db.scalar(select(ExpenseRecord).where(
        ExpenseRecord.user_id == execution.user_id,
        ExpenseRecord.supplier == data["supplier"],
        ExpenseRecord.invoice_number == data["invoice_number"],
    ))
    row_number = connector.find_invoice(data["supplier"], data["invoice_number"])
    if existing or row_number:
        return {"duplicate": True, "message": "Invoice already exists; no row was added.", "row_number": row_number or existing.sheet_row}
    created_at = utcnow().isoformat() + "Z"
    approved = [created_at, data["supplier"], data["invoice_number"], data["amount"], data["currency"], data["due_date"], "Approved for tracking", data["source_email_id"], str(execution.task_id), "completed_verified"]
    row_number = connector.append_row(approved)
    actual = connector.read_row(row_number)
    if actual != approved:
        raise ValueError("Google Sheets read-back did not match approved values")
    db.add(ExpenseRecord(user_id=execution.user_id, task_id=execution.task_id, supplier=data["supplier"], invoice_number=data["invoice_number"], amount=data["amount"], currency=data["currency"], due_date=data["due_date"], source_email_id=data["source_email_id"], sheet_row=row_number, verification_status="completed_verified"))
    return {"duplicate": False, "message": "Expense row added and verified by read-back.", "row_number": row_number, "verification_status": "completed_verified"}
