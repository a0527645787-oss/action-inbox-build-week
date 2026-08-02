from dataclasses import dataclass
import hashlib
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .analysis import analyze_email
from .models import Email


@dataclass(frozen=True)
class TriageResult:
    emails_checked: int
    tasks_created: int
    failures: int = 0


logger = logging.getLogger(__name__)


def triage_unanalyzed_emails(
    db: Session,
    user_id: str,
    *,
    email_ids: list[int] | None = None,
    source: str | None = None,
    continue_on_error: bool = False,
    max_tasks: int | None = None,
) -> TriageResult:
    """Analyze only new email owned by one user through the shared analysis pipeline."""
    query = select(Email).where(
        Email.user_id == user_id,
        Email.analyzed.is_(False),
    )
    if email_ids is not None:
        if not email_ids:
            return TriageResult(0, 0, 0)
        query = query.where(Email.id.in_(email_ids))
    if source is not None:
        query = query.where(Email.source == source)
    emails = db.scalars(query.order_by(Email.received_at)).all()
    tasks_created = failures = checked = 0
    for email in emails:
        if max_tasks is not None and tasks_created >= max_tasks:
            break
        checked += 1
        try:
            analysis = analyze_email(db, email)
        except Exception:
            db.rollback()
            failures += 1
            logger.exception(
                "Email triage failed user_fingerprint=%s email_fingerprint=%s source=%s",
                hashlib.sha256(user_id.encode()).hexdigest()[:12],
                hashlib.sha256(str(email.id).encode()).hexdigest()[:12],
                email.source,
            )
            if continue_on_error:
                continue
            raise
        if analysis.action_required and email.task is not None:
            tasks_created += 1
    return TriageResult(
        emails_checked=checked,
        tasks_created=tasks_created,
        failures=failures,
    )
