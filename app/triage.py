from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .analysis import analyze_email
from .models import Email


@dataclass(frozen=True)
class TriageResult:
    emails_checked: int
    tasks_created: int


def triage_unanalyzed_emails(db: Session, user_id: str) -> TriageResult:
    """Analyze only new email owned by one user through the shared analysis pipeline."""
    emails = db.scalars(
        select(Email)
        .where(Email.user_id == user_id, Email.analyzed.is_(False))
        .order_by(Email.received_at)
    ).all()
    tasks_created = 0
    for email in emails:
        analysis = analyze_email(db, email)
        if analysis.action_required and email.task is not None:
            tasks_created += 1
    return TriageResult(emails_checked=len(emails), tasks_created=tasks_created)
