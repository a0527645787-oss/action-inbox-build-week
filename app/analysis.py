from dataclasses import dataclass
from datetime import datetime
from sqlalchemy.orm import Session
from .models import Analysis, Email, Task

@dataclass(frozen=True)
class Sample:
    classification: str
    actionable: bool
    summary: str
    title: str | None = None
    deadline: datetime | None = None
    deadline_text: str | None = None
    evidence: str | None = None
    suggestion: str | None = None

SAMPLES = {
 "demo-invoice":Sample("invoice",True,"Invoice INV-2048 for USD 1,280 needs approval after checking PO-774.","Approve invoice INV-2048",datetime(2026,7,21),"July 21, 2026","Please approve invoice INV-2048 for USD 1,280 by July 21, 2026.","Verify PO-774 in your records, then prepare the invoice for approval."),
 "demo-meeting":Sample("meeting",True,"Choose one of two supplier-review times and bring June delivery metrics.","Choose supplier review time",datetime(2026,7,20),"July 20","Reply with your preferred time by July 20.","Check your availability for both proposed slots before choosing one."),
 "demo-documents":Sample("action_required",True,"Provide a current W-9 and proof of insurance for vendor renewal.","Send vendor renewal documents",datetime(2026,7,24),"July 24, 2026","We need both documents by July 24, 2026.","Locate both documents and report any unavailable item before the deadline."),
 "demo-info":Sample("informational",False,"The employee entrance is closed Sunday morning; use the visitor entrance.",evidence="No response is required.",suggestion="Plan to use the visitor entrance during the maintenance window."),
 "demo-newsletter":Sample("newsletter_noise",False,"A monthly product and design newsletter with an inert reading link.",evidence="You are receiving this monthly newsletter because you subscribed.",suggestion="No action is needed."),
}

def evidence_offsets(body, quote):
    start = body.find(quote)
    if start < 0:
        raise ValueError("Evidence quote does not exactly match the email body")
    return start, start + len(quote)

def analyze_email(db: Session, email: Email):
    if email.analysis:
        return email.analysis
    sample = SAMPLES[email.external_id]
    start, end = evidence_offsets(email.body, sample.evidence)
    analysis = Analysis(email=email, classification=sample.classification, action_required=sample.actionable, summary=sample.summary, evidence_quote=sample.evidence, evidence_start=start, evidence_end=end, suggestion=sample.suggestion)
    db.add(analysis)
    if sample.actionable:
        db.add(Task(email=email, title=sample.title, deadline=sample.deadline, deadline_text=sample.deadline_text))
    email.analyzed = True
    db.commit(); db.refresh(analysis)
    return analysis

def highlighted_parts(email):
    a = email.analysis
    if not a or a.evidence_start is None:
        return email.body, "", ""
    return email.body[:a.evidence_start], email.body[a.evidence_start:a.evidence_end], email.body[a.evidence_end:]
