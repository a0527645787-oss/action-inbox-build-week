import pytest
from sqlalchemy import func, select
from app.analysis import analyze_email, evidence_offsets
from app.demo_data import load_demo_emails
from app.models import Email, Task

def test_demo_email_loading_is_complete_and_idempotent(db):
    assert len(load_demo_emails(db)) == 5
    load_demo_emails(db)
    assert db.scalar(select(func.count()).select_from(Email)) == 5

def test_actionable_analysis_creates_one_task(db):
    load_demo_emails(db)
    email=db.scalar(select(Email).where(Email.external_id=="demo-invoice"))
    analyze_email(db,email)
    task=db.scalar(select(Task).where(Task.email_id==email.id))
    assert task.title == "Approve invoice INV-2048"
    assert task.deadline_text == "July 21, 2026"
    analyze_email(db,email)
    assert db.scalar(select(func.count()).select_from(Task)) == 1

def test_non_actionable_email_creates_no_task(db):
    load_demo_emails(db)
    email=db.scalar(select(Email).where(Email.external_id=="demo-info"))
    analyze_email(db,email)
    assert db.scalar(select(func.count()).select_from(Task)) == 0

def test_evidence_offsets_match_exact_quote(db):
    load_demo_emails(db)
    email=db.scalar(select(Email).where(Email.external_id=="demo-documents"))
    analysis=analyze_email(db,email)
    assert email.body[analysis.evidence_start:analysis.evidence_end] == analysis.evidence_quote

def test_missing_evidence_is_rejected():
    with pytest.raises(ValueError,match="exactly match"):
        evidence_offsets("Original email text","invented evidence")
