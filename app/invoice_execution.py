"""Evidence-backed, approval-gated Google Sheets invoice append."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from .execution import parse_structured_result
from .models import Execution, SheetAppendRecord, Task, utcnow


SHEETS_TOOL = "append_verified_invoice_row"
SHEET_HEADERS = [
    "Created At", "Proposal ID", "Supplier", "Invoice Number", "Amount",
    "Currency", "Due Date", "Status", "Source Email ID",
    "ActionInbox Task ID", "Verification Status",
]


class SheetsNotConfigured(RuntimeError):
    pass


class SheetsSchemaError(RuntimeError):
    pass


class SheetsTransientError(RuntimeError):
    pass


class SheetsConnector(Protocol):
    sheet_id: str
    tab: str

    def find_proposal(self, proposal_id: str) -> tuple[int, list[str]] | None: ...
    def append_row(self, values: list[str]) -> tuple[int, str]: ...
    def read_row(self, row_number: int) -> list[str]: ...


@dataclass(frozen=True)
class InvoiceDetails:
    supplier: str | None
    invoice_number: str | None
    amount: str | None
    currency: str | None
    due_date: str | None
    source_email_id: str
    missing_fields: list[str]


def sheets_target() -> tuple[str, str] | None:
    if os.getenv("ACTIONINBOX_SHEETS_ENABLED", "false").strip().casefold() != "true":
        return None
    sheet_id = os.getenv("ACTIONINBOX_SHEET_ID", "").strip()
    sheet_tab = os.getenv("ACTIONINBOX_SHEET_TAB", "").strip()
    return (sheet_id, sheet_tab) if sheet_id and sheet_tab else None


def _match(pattern: str, text: str) -> str | None:
    found = re.search(pattern, text, re.IGNORECASE)
    return found.group(1).strip() if found else None


def extract_invoice_details(task: Task) -> InvoiceDetails | None:
    if task.email.source != "gmail" or not task.email.analysis or not task.email.analysis.structured_result:
        return None
    result = parse_structured_result(task.email.analysis.structured_result)
    if result.schema_version != "2" or result.primary_classification != "invoice":
        return None
    supplier = invoice_number = amount = currency = due_date = None
    for fact in result.email_facts:
        evidence = fact.evidence
        if task.email.body[evidence.start_offset:evidence.end_offset] != evidence.exact_quote:
            continue
        quote = evidence.exact_quote
        if fact.type == "amount":
            iso_amount = re.search(r"\b([A-Z]{3})\s*\$?([0-9][0-9,]*(?:\.[0-9]{1,2})?)\b", quote)
            dollar_amount = re.search(r"\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\b", quote)
            if iso_amount:
                currency, amount = iso_amount.group(1).upper(), iso_amount.group(2).replace(",", "")
            elif dollar_amount:
                currency, amount = "USD", dollar_amount.group(1).replace(",", "")
            elif fact.normalized_value:
                normalized = re.search(r"(?:\b([A-Z]{3})\b\s*)?([0-9][0-9,]*(?:\.[0-9]{1,2})?)(?:\s*\b([A-Z]{3})\b)?", fact.normalized_value)
                if normalized and (normalized.group(1) or normalized.group(3)):
                    currency = (normalized.group(1) or normalized.group(3)).upper()
                    amount = normalized.group(2).replace(",", "")
        if fact.type == "deadline":
            due_date = fact.normalized_value or fact.value
        invoice_number = invoice_number or _match(r"\binvoice\s+(?:number\s*[:#]?\s*|#\s*)?([A-Z0-9][A-Z0-9-]+)", quote)
        supplier = supplier or _match(r"\b(?:supplier|vendor|from)\s*[:\-]?\s*([A-Za-z][A-Za-z0-9 &.'-]{1,80}?)(?=\s+(?:invoice|has|for)|[,.;\n])", quote)
    missing = [name for name, value in {
        "supplier": supplier,
        "invoice_number": invoice_number,
        "amount": amount,
        "currency": currency,
        "due_date": due_date,
    }.items() if not value]
    return InvoiceDetails(
        supplier=supplier,
        invoice_number=invoice_number,
        amount=amount,
        currency=currency,
        due_date=due_date,
        source_email_id=task.email.gmail_message_id or task.email.external_id,
        missing_fields=missing,
    )


def _proposal_id(task: Task, sheet_id: str, tab: str) -> str:
    digest = hashlib.sha256(f"{task.user_id}:{task.id}:{sheet_id}:{tab}".encode()).hexdigest()
    return f"aip_{digest[:32]}"


def _row_hash(row: list[str]) -> str:
    payload = json.dumps(row, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def build_invoice_plan(task: Task) -> dict | None:
    details = extract_invoice_details(task)
    if details is None:
        return None
    target = sheets_target()
    if target is None:
        raise SheetsNotConfigured("SHEETS_NOT_CONFIGURED")
    sheet_id, tab = target
    if details.missing_fields:
        raise ValueError("Invoice proposal lacks required evidence-backed fields")
    proposal_id = _proposal_id(task, sheet_id, tab)
    created_at = utcnow().isoformat() + "Z"
    final_row = [
        created_at, proposal_id, details.supplier, details.invoice_number,
        details.amount, details.currency, details.due_date,
        "Approved for tracking", details.source_email_id, str(task.id),
        "completed_verified",
    ]
    mapping = [{"column": header, "value": value} for header, value in zip(SHEET_HEADERS, final_row, strict=True)]
    return {
        "version": 2,
        "task_id": task.id,
        "proposal_id": proposal_id,
        "action_type": "append_verified_invoice_row",
        "actions": [{"order": 1, "tool": SHEETS_TOOL, "description": "Append exactly this approved invoice row and verify it by read-back."}],
        "spreadsheet_id": sheet_id,
        "sheet_tab": tab,
        "ordered_columns": SHEET_HEADERS,
        "column_mapping": mapping,
        "final_row": final_row,
        "row_hash": _row_hash(final_row),
        "invoice": {
            "supplier": details.supplier,
            "invoice_number": details.invoice_number,
            "amount": details.amount,
            "currency": details.currency,
            "due_date": details.due_date,
            "created_at": created_at,
        },
        "external_services": ["Google Sheets API"],
        "information_shared": SHEET_HEADERS,
        "permissions_required": ["Read and append rows in the one configured spreadsheet"],
        "risk_level": "low",
        "reversible": False,
        "external_side_effects": True,
        "success_condition": "The resulting A:K row exactly matches the frozen approved row.",
        "idempotency_key": proposal_id,
        "safety_notes": [
            "The first click creates this proposal only; it does not write to Google Sheets.",
            "Only a separate confirmation can queue this immutable row.",
            "This records an expense; it does not pay an invoice or perform an AWS action.",
        ],
    }


def validate_invoice_plan(task: Task, plan: dict) -> bool:
    details = extract_invoice_details(task)
    target = sheets_target()
    if details is None or details.missing_fields or target is None:
        return False
    expected_invoice = {
        "supplier": details.supplier,
        "invoice_number": details.invoice_number,
        "amount": details.amount,
        "currency": details.currency,
        "due_date": details.due_date,
        "created_at": plan.get("invoice", {}).get("created_at"),
    }
    return bool(
        plan.get("action_type") == "append_verified_invoice_row"
        and (plan.get("spreadsheet_id"), plan.get("sheet_tab")) == target
        and plan.get("proposal_id") == _proposal_id(task, *target)
        and plan.get("invoice") == expected_invoice
        and plan.get("row_hash") == _row_hash(plan.get("final_row", []))
        and plan.get("column_mapping") == [
            {"column": header, "value": value}
            for header, value in zip(SHEET_HEADERS, plan.get("final_row", []), strict=True)
        ]
    )


class GoogleSheetsConnector:
    def __init__(self):
        credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        target = sheets_target()
        if target is None or not credentials_path or not Path(credentials_path).is_file():
            raise SheetsNotConfigured("SHEETS_NOT_CONFIGURED")
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        self.sheet_id, self.tab = target
        credentials = Credentials.from_service_account_file(
            credentials_path,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        self.service = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    def _range(self, cells: str) -> str:
        return f"'{self.tab.replace(chr(39), chr(39) * 2)}'!{cells}"

    def _rows(self) -> list[list[str]]:
        response = self.service.spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range=self._range("A:K"),
        ).execute()
        rows = response.get("values", [])
        if not rows or rows[0] != SHEET_HEADERS:
            raise SheetsSchemaError("SHEET_SCHEMA_MISMATCH")
        return rows

    def find_proposal(self, proposal_id: str) -> tuple[int, list[str]] | None:
        for number, row in enumerate(self._rows()[1:], 2):
            if len(row) >= 2 and row[1] == proposal_id:
                return number, row
        return None

    def append_row(self, values: list[str]) -> tuple[int, str]:
        response = self.service.spreadsheets().values().append(
            spreadsheetId=self.sheet_id,
            range=self._range("A:K"),
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [values]},
        ).execute()
        updated_range = response["updates"]["updatedRange"]
        row_number = int(re.search(r"(\d+)(?::[A-Z]+\d+)?$", updated_range).group(1))
        return row_number, updated_range

    def read_row(self, row_number: int) -> list[str]:
        response = self.service.spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range=self._range(f"A{row_number}:K{row_number}"),
        ).execute()
        return (response.get("values") or [[]])[0]


def _receipt(execution: Execution, plan: dict, row_number: int, sheet_range: str) -> dict:
    return {
        "execution_id": execution.id,
        "task_id": execution.task_id,
        "tool_name": SHEETS_TOOL,
        "spreadsheet_id": plan["spreadsheet_id"],
        "sheet_tab": plan["sheet_tab"],
        "sheet_range": sheet_range,
        "row_number": row_number,
        "proposal_id": plan["proposal_id"],
        "plan_hash": execution.plan_hash,
        "verification_status": "completed_verified",
        "message": "Approved invoice row appended exactly once and verified by read-back.",
    }


def _verified_receipt(execution: Execution, plan: dict, row_number: int, sheet_range: str, row: list[str]) -> dict:
    if row != plan["final_row"]:
        raise ValueError("SHEET_READBACK_MISMATCH")
    return _receipt(execution, plan, row_number, sheet_range)


def execute_invoice_plan(db: Session, execution: Execution, connector: SheetsConnector) -> dict:
    plan = json.loads(execution.plan)
    if plan.get("proposal_id") is None or plan.get("row_hash") != _row_hash(plan.get("final_row", [])):
        raise RuntimeError("FROZEN_ROW_INVALID")
    if connector.sheet_id != plan["spreadsheet_id"] or connector.tab != plan["sheet_tab"]:
        raise SheetsNotConfigured("SHEETS_TARGET_MISMATCH")
    stored = db.scalar(select(SheetAppendRecord).where(SheetAppendRecord.proposal_id == plan["proposal_id"]))
    if stored:
        row_match = re.search(r"!A(\d+):K\d+$", stored.sheet_range)
        if not row_match:
            raise RuntimeError("STORED_SHEET_RANGE_INVALID")
        return _receipt(execution, plan, int(row_match.group(1)), stored.sheet_range)
    try:
        found = connector.find_proposal(plan["proposal_id"])
    except (SheetsSchemaError, SheetsNotConfigured):
        raise
    except Exception as exc:
        raise SheetsTransientError("SHEETS_PROVIDER_LOOKUP_FAILED") from exc
    if found:
        row_number, row = found
        sheet_range = f"'{plan['sheet_tab'].replace(chr(39), chr(39) * 2)}'!A{row_number}:K{row_number}"
    else:
        try:
            row_number, sheet_range = connector.append_row(plan["final_row"])
        except Exception as exc:
            try:
                found = connector.find_proposal(plan["proposal_id"])
            except Exception as lookup_exc:
                raise SheetsTransientError("SHEETS_PROVIDER_AMBIGUOUS") from lookup_exc
            if not found:
                raise SheetsTransientError("SHEETS_PROVIDER_AMBIGUOUS") from exc
            row_number, row = found
            sheet_range = f"'{plan['sheet_tab'].replace(chr(39), chr(39) * 2)}'!A{row_number}:K{row_number}"
        else:
            try:
                row = connector.read_row(row_number)
            except Exception as exc:
                raise SheetsTransientError("SHEETS_READBACK_UNAVAILABLE") from exc
    receipt = _verified_receipt(execution, plan, row_number, sheet_range, row)
    db.add(SheetAppendRecord(
        proposal_id=plan["proposal_id"],
        user_id=execution.user_id,
        task_id=execution.task_id,
        execution_id=execution.id,
        spreadsheet_id=plan["spreadsheet_id"],
        sheet_tab=plan["sheet_tab"],
        sheet_range=sheet_range,
        row_hash=plan["row_hash"],
        verification_status="completed_verified",
    ))
    return receipt
