import hashlib
import hmac
import json
import threading
import time
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from app.auth import get_current_user
from app.database import get_db
from app.main import app
from app.models import Email, GmailCredential, GmailSyncJob, Task, utcnow
from app.gmail import GMAIL_SCOPE, enqueue_gmail_sync, claim_gmail_sync_job, run_gmail_sync_job
from app.sync_analysis import analyze_sync_batch
from app.presentation import sync_presentation
from test_gmail_and_mcp import _personal_user, _token, GmailClient
from app.analysis import fallback_analysis
from app.demo_data import DEMO_EMAILS


def client_for(db, user):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    token=hmac.new(b'test-session-secret-at-least-thirty-two-bytes',b'action-csrf:',hashlib.sha256).hexdigest()
    return TestClient(app,base_url='https://testserver',headers={'X-CSRF-Token':token})


def setup_job(db, monkeypatch):
    user=_personal_user(db)
    credential=GmailCredential(user_id=user.id,account_email='pilot@example.test',encrypted_token=_token(monkeypatch),scopes=GMAIL_SCOPE)
    db.add(credential);db.commit()
    return user, enqueue_gmail_sync(db,user,credential)


def test_browser_sync_redirects_to_inbox_and_api_returns_json_only_when_requested(db,monkeypatch):
    user,job=setup_job(db,monkeypatch)
    try:
        with client_for(db,user) as client:
            response=client.post('/gmail/sync',headers={'Accept':'text/html'})
            assert response.status_code==200 and str(response.url).endswith('/inbox')
            assert 'Checking new emails…' in response.text and 'data-sync-form' in response.text
            assert '"job_id"' not in response.text
            api=client.post('/gmail/sync',headers={'Accept':'application/json'})
            assert api.status_code==202 and api.json()['job_id']==job.id
            assert client.post('/gmail/sync',headers={'X-CSRF-Token':'bad'}).status_code==403
            expired=client.post('/gmail/sync',headers={'X-CSRF-Token':'bad','Accept':'text/html'})
            assert expired.status_code==200 and '/inbox' in str(expired.url)
            assert '"detail"' not in expired.text
            job.status='succeeded';job.imported=2;job.candidates=12;job.tasks_created=1;job.active_slot=None;db.commit()
            page=client.get('/inbox')
            assert 'Checked 12 emails · found 2 new emails · created 1 task.' in page.text
            job.status='failed';job.safe_error='PRIVATE-ERROR';db.commit()
            page=client.get('/inbox')
            assert 'Try again' in page.text and 'PRIVATE-ERROR' not in page.text
    finally:app.dependency_overrides.clear()


def test_invalid_evidence_is_never_projected_or_retried(db,monkeypatch):
    user,job=setup_job(db,monkeypatch);result=synthetic_batch(db,user,job,count=1)
    result.email_facts[0].evidence.exact_quote='unsupported text'
    # Corrupt all support so the exact-evidence validator rejects the task.
    for fact in result.email_facts:
        fact.evidence.exact_quote='unsupported text'
    calls=[]
    def invalid(*_,**__):
        calls.append(1)
        return result
    analyze_sync_batch(db,job,lambda _:None,analyze=invalid)
    assert job.tasks_created==0 and job.analysis_failures==1
    analyze_sync_batch(db,job,lambda _:None,analyze=invalid)
    assert len(calls)==1 and not db.scalars(select(Task)).all()


def test_empty_sync_and_timings_do_not_analyze_existing_email(db,monkeypatch,caplog):
    user,job=setup_job(db,monkeypatch)
    job.created_at=utcnow()-timedelta(seconds=2);db.commit()
    claim_gmail_sync_job(db)
    monkeypatch.setattr('app.sync_analysis.request_live_analysis',lambda *_args,**_kwargs: (_ for _ in ()).throw(RuntimeError('private-body-token')))
    run_gmail_sync_job(db,job,client=GmailClient())
    assert job.imported==1 and job.analysis_failures==1 and job.queue_wait_ms>=1900
    assert job.fetch_ms>=0 and job.storage_ms>=0 and job.triage_ms>=0
    email=db.scalar(select(Email).where(Email.sync_job_id==job.id))
    assert email.sync_analysis_attempted and not email.analyzed
    second=enqueue_gmail_sync(db,user,db.get(GmailCredential,job.credential_id))
    run_gmail_sync_job(db,second,client=GmailClient())
    assert second.imported==0 and second.analysis_failures==0
    assert 'No new emails' in sync_presentation(second)['message']
    assert 'private-body-token' not in caplog.text


def synthetic_batch(db,user,job,count=4):
    # Same fully synthetic evidence used by the existing deterministic demo tests.
    item=next(e for e in DEMO_EMAILS if e['external_id']=='demo-documents')
    sample=Email(**item,user_id=user.id,source='demo')
    result=fallback_analysis(sample,[])
    for n in range(count):
        db.add(Email(user_id=user.id,external_id=f'synthetic-{n}',source='gmail',sender=sample.sender,
                     subject=sample.subject,body=sample.body,received_at=utcnow(),sync_job_id=job.id,analyzed=False))
    db.commit()
    return result


def test_two_bounded_analyses_persist_in_order_without_reprocessing(db,monkeypatch):
    user,job=setup_job(db,monkeypatch);result=synthetic_batch(db,user,job)
    active=peak=calls=0;lock=threading.Lock()
    def analyze(*_,**__):
        nonlocal active,peak,calls
        with lock: active+=1;peak=max(peak,active);calls+=1
        time.sleep(.02)
        with lock:active-=1
        return result.model_copy(deep=True)
    analyze_sync_batch(db,job,lambda _:None,analyze=analyze)
    assert peak==2 and calls==4 and job.tasks_created==4 and job.analysis_failures==0
    assert [t.email_id for t in db.scalars(select(Task).order_by(Task.id))]==sorted(t.email_id for t in db.scalars(select(Task)))
    analyze_sync_batch(db,job,lambda _:None,analyze=analyze)
    assert calls==4


def test_task_focus_keeps_provenance_collapsed_and_has_one_primary_action(db):
    from table_fixtures import _invoice_task
    from test_table_actions import destination
    user,task=_invoice_task(db);destination(db,user)
    try:
        with client_for(db,user) as client:
            html=client.get(f'/tasks/{task.id}').text
            assert html.index('Recommended next step') < html.index('Why am I seeing this?')
            assert '<summary>View source email</summary>' in html and '<summary>Technical details</summary>' in html
            assert '<details open' not in html
            assert html.count('class="button"')==1 and 'Add to a table' in html
            visible=html.split('<details')[0]
            assert 'characters' not in visible and 'Re-analyze' not in visible and 'live_gpt' not in visible
    finally:app.dependency_overrides.clear()
