"""Existing evidence-only invoice field extraction; no connector or execution policy."""
import re
from dataclasses import dataclass
from .models import Task
from .execution import parse_structured_result

@dataclass(frozen=True)
class InvoiceDetails:
    supplier: str | None
    invoice_number: str | None
    amount: str | None
    currency: str | None
    due_date: str | None
    source_email_id: str
    missing_fields: list[str]


def _match(pattern: str, text: str) -> str | None:
    found = re.search(pattern, text, re.IGNORECASE)
    return found.group(1).strip() if found else None


def _single_amount_from_quote(quote: str) -> str | None:
    """Return one unambiguous numeric amount copied from accepted evidence."""
    matches = re.findall(r"\b[0-9][0-9,]*(?:\.[0-9]{1,2})?\b", quote)
    distinct = list(dict.fromkeys(value.replace(",", "") for value in matches))
    return distinct[0] if len(distinct) == 1 else None


def _iso_currency_from_fact(fact, quote: str) -> str | None:
    """Accept an ISO currency only when the accepted quote contains it verbatim."""
    for candidate in (fact.normalized_value, fact.value):
        currency = (candidate or "").strip().upper()
        if re.fullmatch(r"[A-Z]{3}", currency) and re.search(
            rf"\b{re.escape(currency)}\b", quote, re.IGNORECASE
        ):
            return currency
    return None


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
            else:
                amount = _single_amount_from_quote(quote)
            if amount is None and fact.normalized_value:
                normalized = re.search(r"(?:\b([A-Z]{3})\b\s*)?([0-9][0-9,]*(?:\.[0-9]{1,2})?)(?:\s*\b([A-Z]{3})\b)?", fact.normalized_value)
                if normalized and (normalized.group(1) or normalized.group(3)):
                    currency = (normalized.group(1) or normalized.group(3)).upper()
                    amount = normalized.group(2).replace(",", "")
        currency = currency or _iso_currency_from_fact(fact, quote)
        if fact.type == "deadline":
            due_date = fact.normalized_value or fact.value
        invoice_number = invoice_number or _match(r"\binvoice\s+(?:number\s*[:#]?\s*|#\s*)?([A-Z0-9][A-Z0-9-]+)", quote)
        supplier = supplier or _match(r"\b(?:supplier|vendor|from)\s*[:\-]?\s*([A-Za-z][A-Za-z0-9 &.'-]{1,80}?)(?=\s+(?:invoice|has|for)|[,.;\n]|\s*$)", quote)
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
