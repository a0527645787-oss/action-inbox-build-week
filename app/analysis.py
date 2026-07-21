import json
import os
from datetime import datetime

from sqlalchemy.orm import Session

from .models import Analysis, Email, Task
from .openai_analysis import LiveAnalysisError, MODEL, request_live_analysis, validate_evidence
from .schemas import EmailAnalysisResult
from .resources import first_resource_sentence, resource_locator, select_relevant_resources


FALLBACK_RESULTS = {
    "demo-invoice": {
        "primary_classification": "invoice", "action_required": True,
        "summary": "Invoice INV-2048 for USD 1,280 needs approval after checking PO-774.",
        "tasks": [{"id":"task-1","title":"Approve invoice INV-2048","due_at":"2026-07-21T00:00:00","due_text":"July 21, 2026","uncertainty":None,"evidence_ids":["evidence-deadline","evidence-amount"]}],
        "email_facts": [
            {"id":"fact-deadline","type":"deadline","value":"July 21, 2026","normalized_value":"2026-07-21","confidence":"high","uncertainty":None,"evidence":{"id":"evidence-deadline","exact_quote":"Please approve invoice INV-2048 for USD 1,280 by July 21, 2026.","start_offset":0,"end_offset":67}},
            {"id":"fact-amount","type":"amount","value":"USD 1,280","normalized_value":"1280 USD","confidence":"high","uncertainty":None,"evidence":{"id":"evidence-amount","exact_quote":"Please approve invoice INV-2048 for USD 1,280 by July 21, 2026.","start_offset":0,"end_offset":67}},
        ],
        "resource_guidance": [], "ai_suggestions": [{"type":"next_step","text":"Verify PO-774 in your records, then prepare the invoice for approval.","supporting_fact_ids":["fact-deadline","fact-amount"],"supporting_guidance_ids":[],"uncertainty":None}], "missing_information": []},
    "demo-meeting": {
        "primary_classification":"meeting","action_required":True,"summary":"Choose one of two supplier-review times and bring June delivery metrics.",
        "tasks":[{"id":"task-1","title":"Choose supplier review time","due_at":"2026-07-20T00:00:00","due_text":"July 20","uncertainty":None,"evidence_ids":["evidence-deadline"]}],
        "email_facts":[{"id":"fact-deadline","type":"deadline","value":"July 20","normalized_value":"2026-07-20","confidence":"high","uncertainty":None,"evidence":{"id":"evidence-deadline","exact_quote":"Reply with your preferred time by July 20.","start_offset":119,"end_offset":162}}],
        "resource_guidance":[],"ai_suggestions":[{"type":"next_step","text":"Check your availability for both proposed slots before choosing one.","supporting_fact_ids":["fact-deadline"],"supporting_guidance_ids":[],"uncertainty":None}],"missing_information":[]},
    "demo-documents": {
        "primary_classification":"action_required","action_required":True,"summary":"Provide a current W-9 and proof of insurance for vendor renewal.",
        "tasks":[{"id":"task-1","title":"Send vendor renewal documents","due_at":"2026-07-24T00:00:00","due_text":"July 24, 2026","uncertainty":None,"evidence_ids":["evidence-deadline","evidence-w9","evidence-insurance"]}],
        "email_facts":[
            {"id":"fact-deadline","type":"deadline","value":"July 24, 2026","normalized_value":"2026-07-24","confidence":"high","uncertainty":None,"evidence":{"id":"evidence-deadline","exact_quote":"We need both documents by July 24, 2026.","start_offset":82,"end_offset":125}},
            {"id":"fact-w9","type":"required_document","value":"W-9 form","normalized_value":None,"confidence":"high","uncertainty":None,"evidence":{"id":"evidence-w9","exact_quote":"send your current W-9 form and proof of insurance","start_offset":32,"end_offset":81}},
            {"id":"fact-insurance","type":"required_document","value":"proof of insurance","normalized_value":None,"confidence":"high","uncertainty":None,"evidence":{"id":"evidence-insurance","exact_quote":"send your current W-9 form and proof of insurance","start_offset":32,"end_offset":81}}],
        "resource_guidance":[],"ai_suggestions":[{"type":"next_step","text":"Locate both documents and report any unavailable item before the deadline.","supporting_fact_ids":["fact-deadline","fact-w9","fact-insurance"],"supporting_guidance_ids":[],"uncertainty":None}],"missing_information":[]},
    "demo-info": {
        "primary_classification":"informational","action_required":False,"summary":"The employee entrance is closed Sunday morning; use the visitor entrance.","tasks":[],
        "email_facts":[{"id":"fact-info","type":"other","value":"No response is required","normalized_value":None,"confidence":"high","uncertainty":None,"evidence":{"id":"evidence-info","exact_quote":"No response is required.","start_offset":136,"end_offset":160}}],
        "resource_guidance":[],"ai_suggestions":[{"type":"next_step","text":"Plan to use the visitor entrance during the maintenance window.","supporting_fact_ids":["fact-info"],"supporting_guidance_ids":[],"uncertainty":None}],"missing_information":[]},
    "demo-newsletter": {
        "primary_classification":"newsletter_noise","action_required":False,"summary":"A monthly product and design newsletter with an inert reading link.","tasks":[],
        "email_facts":[{"id":"fact-link","type":"important_link","value":"https://productweekly.example/july","normalized_value":None,"confidence":"high","uncertainty":None,"evidence":{"id":"evidence-link","exact_quote":"https://productweekly.example/july","start_offset":105,"end_offset":139}}],
        "resource_guidance":[],"ai_suggestions":[{"type":"next_step","text":"No action is needed.","supporting_fact_ids":[],"supporting_guidance_ids":[],"uncertainty":None}],"missing_information":[]},
}


def evidence_offsets(body, quote):
    start = body.find(quote)
    if start < 0:
        raise ValueError("Evidence quote does not exactly match the email body")
    return start, start + len(quote)


def fallback_analysis(email: Email, resources=None) -> EmailAnalysisResult:
    result_key = email.external_id
    if result_key not in FALLBACK_RESULTS:
        signatures = {
            "demo-invoice": "invoice INV-2048",
            "demo-meeting": "supplier review",
            "demo-documents": "vendor renewal",
            "demo-info": "No response is required.",
            "demo-newsletter": "productweekly.example/july",
        }
        result_key = next((key for key, signature in signatures.items() if signature.casefold() in email.body.casefold()), "")
    if result_key not in FALLBACK_RESULTS:
        raise ValueError("Deterministic fallback is available only for supported synthetic demo email")
    raw = json.loads(json.dumps(FALLBACK_RESULTS[result_key]))
    for fact in raw["email_facts"]:
        quote = fact["evidence"]["exact_quote"]
        start, end = evidence_offsets(email.body, quote)
        fact["evidence"]["start_offset"] = start
        fact["evidence"]["end_offset"] = end
    for index,resource in enumerate(resources or [],1):
        quote=first_resource_sentence(resource); start,end=resource_locator(resource,quote)
        raw["resource_guidance"].append({"id":f"guidance-{index}","resource_id":f"resource-{resource.id}","resource_title":resource.title,"instruction":f"Apply the relevant guidance from {resource.title}.","related_fact_ids":[],"resource_evidence":{"exact_quote":quote,"section":resource.organization_team,"start_offset":start,"end_offset":end}})
    raw["execution_guidance"] = _fallback_execution_guidance(result_key, raw["resource_guidance"])
    return validate_evidence(EmailAnalysisResult.model_validate(raw), email.body, resources)


def _execution_item(text, source="AI_RECOMMENDATION", facts=None, guidance=None):
    return {"text": text, "source": source, "supporting_fact_ids": facts or [], "supporting_guidance_ids": guidance or []}


def _fallback_execution_guidance(external_id: str, resource_guidance: list[dict]):
    resource_steps = [
        _execution_item(item["instruction"], "BUSINESS_GUIDANCE", guidance=[item["id"]])
        for item in resource_guidance
    ]
    if external_id == "demo-invoice":
        return {
            "outcome": _execution_item("Invoice INV-2048 is reviewed against PO-774 and is ready for an authorized approval decision."),
            "ordered_steps": [
                _execution_item("Confirm that purchase order PO-774 appears in your records.", "AI_RECOMMENDATION", ["fact-deadline", "fact-amount"]),
                *resource_steps,
                _execution_item("Review the USD 1,280 invoice and prepare an approval decision before July 21, 2026.", "EMAIL_FACT", ["fact-deadline", "fact-amount"]),
            ],
            "required_inputs": [_execution_item("Invoice PDF and the matching PO-774 record.", "MISSING_UNCERTAIN")],
            "missing_information": ["Whether PO-774 appears in the user's records and who has final approval authority."],
            "safety_checks": [_execution_item("Verify the invoice, amount, purchase order, and approval authority before approving payment.")],
            "proposed_deliverable": _execution_item("An invoice review checklist and approval-decision brief."),
            "recommended_executor": "USER", "executor_explanation": "A person must inspect the attachment and make the payment approval decision; ActionInbox can prepare the checklist.", "readiness": "NEEDS_APPROVAL",
        }
    if external_id == "demo-meeting":
        return {
            "outcome": _execution_item("A preferred supplier-review time is selected and a reply is prepared by July 20.", "AI_RECOMMENDATION", ["fact-deadline"]),
            "ordered_steps": [
                _execution_item("Check availability for the two times stated in the email."),
                _execution_item("Choose the preferred time and gather the June delivery metrics."),
                *resource_steps,
                _execution_item("Prepare a reply with the selected time before July 20.", "EMAIL_FACT", ["fact-deadline"]),
            ],
            "required_inputs": [_execution_item("The user's availability and June delivery metrics.", "MISSING_UNCERTAIN")],
            "missing_information": ["Which proposed meeting time the user prefers."],
            "safety_checks": [_execution_item("Verify the chosen time and recipient before sending a reply.")],
            "proposed_deliverable": _execution_item("A meeting-time reply draft and preparation checklist."),
            "recommended_executor": "FUTURE_CONNECTOR", "executor_explanation": "ActionInbox can prepare the reply, but sending email or checking a connected calendar requires an unavailable connector and user approval.", "readiness": "INTEGRATION_REQUIRED",
        }
    if external_id == "demo-documents":
        return {
            "outcome": _execution_item("The current W-9 form and proof of insurance are assembled for vendor renewal by July 24, 2026.", "EMAIL_FACT", ["fact-deadline", "fact-w9", "fact-insurance"]),
            "ordered_steps": [
                _execution_item("Locate the current W-9 form and proof of insurance.", "EMAIL_FACT", ["fact-w9", "fact-insurance"]),
                *resource_steps,
                _execution_item("Check that both documents are current and prepare them for review."),
                _execution_item("If either document is unavailable, prepare a notice before the deadline.", "AI_RECOMMENDATION", ["fact-deadline"]),
            ],
            "required_inputs": [
                _execution_item("Current W-9 form.", "EMAIL_FACT", ["fact-w9"]),
                _execution_item("Proof of insurance.", "EMAIL_FACT", ["fact-insurance"]),
            ],
            "missing_information": ["Whether both current documents are available and the approved delivery method."],
            "safety_checks": [_execution_item("Confirm both documents are current and verify the recipient before sharing sensitive business records.")],
            "proposed_deliverable": _execution_item("A document checklist and a vendor-renewal reply draft for user review."),
            "recommended_executor": "CHATGPT_WORK", "executor_explanation": "Work can help prepare the checklist and draft, while the user must inspect the documents and approve any external sharing.", "readiness": "NEEDS_INFORMATION",
        }
    return None


def _evidence_backed_task_title(task, result: EmailAnalysisResult) -> str:
    facts_by_evidence_id = {fact.evidence.id: fact for fact in result.email_facts}
    cited_documents = [
        facts_by_evidence_id[item]
        for item in task.evidence_ids
        if item in facts_by_evidence_id and facts_by_evidence_id[item].type == "required_document"
    ]
    if not cited_documents:
        return task.title
    quotes = {fact.evidence.exact_quote.strip().rstrip(".") for fact in cited_documents}
    if len(quotes) != 1:
        document_names = [fact.value.strip() for fact in cited_documents]
        if document_names[0].casefold().startswith("current "):
            document_names[0] = "the " + document_names[0]
        if len(document_names) == 1:
            return "Send " + document_names[0]
        if len(document_names) == 2:
            return "Send " + " and ".join(document_names)
        return "Send " + ", ".join(document_names[:-1]) + ", and " + document_names[-1]
    quote = quotes.pop()
    if len(quote) > 255 or not all(fact.value.casefold() in quote.casefold() for fact in cited_documents):
        return task.title
    if not quote.casefold().startswith(("send ", "provide ", "submit ")):
        return task.title
    title = quote[0].upper() + quote[1:]
    if title.casefold().startswith("send your current "):
        title = "Send the current " + title[len("Send your current "):]
    return title


def _evidence_backed_deadline_text(task, result: EmailAnalysisResult) -> str | None:
    facts_by_evidence_id = {fact.evidence.id: fact for fact in result.email_facts}
    deadline_fact = next(
        (facts_by_evidence_id[item] for item in task.evidence_ids if item in facts_by_evidence_id and facts_by_evidence_id[item].type == "deadline"),
        None,
    )
    return deadline_fact.value if deadline_fact else task.due_text


def _project_result(db: Session, email: Email, result: EmailAnalysisResult, source: str, error: str | None):
    facts_by_evidence_id = {fact.evidence.id: fact for fact in result.email_facts}
    projected_task = result.tasks[0] if result.action_required and result.tasks else None
    evidence = None
    if projected_task:
        evidence = next((facts_by_evidence_id[item].evidence for item in projected_task.evidence_ids if item in facts_by_evidence_id), None)
    if evidence is None and result.email_facts:
        evidence = result.email_facts[0].evidence
    suggestion = next((item.text for item in result.ai_suggestions if item.type == "next_step"), None)
    analysis = Analysis(
        email=email, user_id=email.user_id, classification=result.primary_classification, action_required=bool(projected_task), summary=result.summary,
        evidence_quote=evidence.exact_quote if evidence else None, evidence_start=evidence.start_offset if evidence else None,
        evidence_end=evidence.end_offset if evidence else None, suggestion=suggestion,
        structured_result=result.model_dump_json(), source=source, model=MODEL if source == "live_gpt" else None, error_message=error,
    )
    db.add(analysis)
    if projected_task:
        deadline = None
        if projected_task.due_at:
            deadline = datetime.fromisoformat(projected_task.due_at.replace("Z", "+00:00")).replace(tzinfo=None)
        title = _evidence_backed_task_title(projected_task, result)
        deadline_text = _evidence_backed_deadline_text(projected_task, result)
        db.add(Task(email=email, user_id=email.user_id, title=title, deadline=deadline, deadline_text=deadline_text))
    email.analyzed = True
    db.commit(); db.refresh(analysis)
    return analysis


def analyze_email(db: Session, email: Email, *, force: bool = False, client=None):
    if email.analysis and not force:
        return email.analysis
    if force:
        if email.task:
            db.delete(email.task)
        if email.analysis:
            db.delete(email.analysis)
        db.flush()
    source = "demo_fallback"
    error = None
    resources=select_relevant_resources(db,email)
    if email.source == "gmail" and client is None and not os.getenv("OPENAI_API_KEY"):
        raise LiveAnalysisError("Live analysis is unavailable for this email")
    try:
        if client is not None or os.getenv("OPENAI_API_KEY"):
            result = request_live_analysis(email, client=client, resources=resources)
            source = "live_gpt"
        else:
            result = fallback_analysis(email,resources)
    except Exception as exc:
        if email.source == "gmail":
            if isinstance(exc, LiveAnalysisError):
                raise
            raise LiveAnalysisError("OpenAI analysis failed") from exc
        result = fallback_analysis(email,resources)
        error = str(exc) if isinstance(exc, LiveAnalysisError) else "Live analysis returned invalid output"
    return _project_result(db, email, result, source, error)


def highlighted_parts(email):
    analysis = email.analysis
    if not analysis or analysis.evidence_start is None:
        return email.body, "", ""
    return email.body[:analysis.evidence_start], email.body[analysis.evidence_start:analysis.evidence_end], email.body[analysis.evidence_end:]
