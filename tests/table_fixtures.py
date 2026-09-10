import json
from datetime import UTC, datetime
from app.models import Analysis, Email, Task, User

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



