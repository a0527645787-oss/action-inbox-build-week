"""Two independent model reads, followed by ordered writes on the worker's session."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from time import perf_counter

from sqlalchemy import select, func

from .analysis import _project_result
from .models import Email, Task
from .openai_analysis import request_live_analysis, validate_evidence, MAX_EMAIL_CHARS
from .resources import select_relevant_resources

ANALYSIS_CONCURRENCY = 2


def analyze_sync_batch(db, job, heartbeat, *, concurrency=ANALYSIS_CONCURRENCY, analyze=None):
    """Never use ORM objects/sessions in model threads or revisit already attempted mail."""
    started = perf_counter()
    analyze = analyze or request_live_analysis
    job.phase = "analyzing"
    db.commit()
    try:
        while True:
            emails = db.scalars(select(Email).where(Email.user_id == job.user_id,
                Email.sync_job_id == job.id, Email.analyzed.is_(False),
                Email.sync_analysis_attempted.is_(False)).order_by(Email.received_at, Email.id).limit(min(max(concurrency, 1), 2))).all()
            if not emails:
                break
            inputs = []
            for email in emails:
                resources = [SimpleNamespace(**{key: getattr(r, key) for key in ("id", "title", "resource_type", "content", "enabled")}) for r in select_relevant_resources(db, email)]
                inputs.append((SimpleNamespace(sender=email.sender, subject=email.subject, body=email.body), resources))
                email.sync_analysis_attempted = True
            heartbeat(job)
            db.commit()
            with ThreadPoolExecutor(max_workers=len(emails)) as pool:
                futures = [pool.submit(analyze, snapshot, resources=resources) for snapshot, resources in inputs]
                # Submission order, not completion order, determines persisted task order.
                for email, (snapshot, _), future in zip(emails, inputs, futures):
                    try:
                        result = future.result()
                        db.refresh(email)
                        if email.analyzed or email.analysis:
                            continue
                        if email.body != snapshot.body:
                            continue
                        resources = select_relevant_resources(db, email)
                        result = validate_evidence(result, email.body[:MAX_EMAIL_CHARS], resources)
                        _project_result(db, email, result, "live_gpt", None)
                    except Exception:
                        db.rollback()
                        # No exception text, message identifiers or content is logged.
                    heartbeat(job)
                    db.commit()
        job.tasks_created = db.scalar(select(func.count(Task.id)).join(Email, Task.email_id == Email.id).where(Email.sync_job_id == job.id, Email.user_id == job.user_id))
        job.analysis_failures = db.scalar(select(func.count(Email.id)).where(Email.sync_job_id == job.id, Email.user_id == job.user_id, Email.analyzed.is_(False)))
    finally:
        job.triage_ms += round((perf_counter() - started) * 1000)
        db.commit()
