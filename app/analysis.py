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
    raw = json.loads(json.dumps(FALLBACK_RESULTS[email.external_id]))
    for fact in raw["email_facts"]:
        quote = fact["evidence"]["exact_quote"]
        start, end = evidence_offsets(email.body, quote)
        fact["evidence"]["start_offset"] = start
        fact["evidence"]["end_offset"] = end
    for index,resource in enumerate(resources or [],1):
        quote=first_resource_sentence(resource); start,end=resource_locator(resource,quote)
        raw["resource_guidance"].append({"id":f"guidance-{index}","resource_id":f"resource-{resource.id}","resource_title":resource.title,"instruction":f"Apply the relevant guidance from {resource.title}.","related_fact_ids":[],"resource_evidence":{"exact_quote":quote,"section":resource.organization_team,"start_offset":start,"end_offset":end}})
    return validate_evidence(EmailAnalysisResult.model_validate(raw), email.body, resources)


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
        email=email, classification=result.primary_classification, action_required=bool(projected_task), summary=result.summary,
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
        db.add(Task(email=email, title=title, deadline=deadline, deadline_text=deadline_text))
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
    try:
        if client is not None or os.getenv("OPENAI_API_KEY"):
            result = request_live_analysis(email, client=client, resources=resources)
            source = "live_gpt"
        else:
            result = fallback_analysis(email,resources)
    except Exception as exc:
        result = fallback_analysis(email,resources)
        error = str(exc) if isinstance(exc, LiveAnalysisError) else "Live analysis returned invalid output"
    return _project_result(db, email, result, source, error)


def highlighted_parts(email):
    analysis = email.analysis
    if not analysis or analysis.evidence_start is None:
        return email.body, "", ""
    return email.body[:analysis.evidence_start], email.body[analysis.evidence_start:analysis.evidence_end], email.body[analysis.evidence_end:]
