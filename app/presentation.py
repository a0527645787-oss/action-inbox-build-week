"""User-facing copy only; never changes a stored proposal or analysis."""
import json
from .table_actions import task_values, SYSTEM_FIELDS

ACTION_LABELS = {"awaiting_approval": "Ready for your review", "queued": "Getting ready", "running": "Adding your row…", "completed_verified": "Added and verified", "succeeded": "Done", "failed": "Needs your attention", "verification_failed": "Needs your attention", "cancelled": "Cancelled"}


def task_presentation(task, result, destinations):
    values = task_values(task)
    choices = []
    for destination in destinations:
        missing = [header for header, field in zip(json.loads(destination.schema_snapshot), json.loads(destination.column_mapping)) if field not in SYSTEM_FIELDS and field != "blank" and not values.get(field)]
        choices.append({"destination": destination, "missing": missing})
    next_step = next((s.text for s in result.ai_suggestions if s.type == "next_step"), "Review the request and its due date before taking the next step.")
    return {"next_step": next_step, "choices": choices,
            "blocking": result.execution_guidance.missing_information if result.execution_guidance and result.execution_guidance.readiness == "NEEDS_INFORMATION" else []}


def sync_presentation(job):
    if job is None:
        return {"active": False, "message": "Check your inbox when you’re ready.", "failed": False}
    active = job.status in {"queued", "running"}
    if active:
        message = "Finding tasks in your new emails…" if job.phase == "analyzing" else "Checking new emails…"
    elif job.status == "failed":
        message = "We couldn’t finish checking your emails. Please try again."
    elif job.imported == 0:
        message = "You’re up to date. No new emails found."
    else:
        message = f"Checked {job.candidates} emails · found {job.imported} new emails · created {job.tasks_created} {'task' if job.tasks_created == 1 else 'tasks'}."
    if job.status == "partial":
        message += " Some emails still need attention. Review your new messages in the Inbox; checking again won’t process saved emails twice."
    return {"active": active, "message": message, "failed": job.status in {"failed", "partial"}, "reconnect": job.safe_error in {"RECONNECT_REQUIRED", "CREDENTIAL_UNAVAILABLE"}}
