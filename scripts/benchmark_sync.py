"""Controlled pipeline comparison for CI; synthetic data and fixed provider delay only."""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.database import Base
from app.models import User, GmailCredential, GmailSyncJob, Email
from app.demo_data import DEMO_EMAILS
from app.analysis import fallback_analysis
from app.sync_analysis import analyze_sync_batch


def measure(concurrency):
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(id='synthetic',email='synthetic@example.test',display_name='Synthetic'));db.commit()
        cred=GmailCredential(user_id='synthetic',account_email='synthetic@example.test',encrypted_token='unused',scopes='unused')
        db.add(cred);db.commit()
        job=GmailSyncJob(user_id='synthetic',credential_id=cred.id,status='running');db.add(job);db.commit()
        data=next(item for item in DEMO_EMAILS if item['external_id']=='demo-documents')
        sample=Email(**data,user_id='synthetic',source='demo')
        result=fallback_analysis(sample,[])
        for n in range(6):
            db.add(Email(**{**data,'external_id':f'synthetic-{n}'},user_id='synthetic',source='gmail',sync_job_id=job.id))
        db.commit()
        def provider(*args,**kwargs):
            time.sleep(.1)
            return result.model_copy(deep=True)
        started=time.perf_counter()
        analyze_sync_batch(db,job,lambda _:None,concurrency=concurrency,analyze=provider)
        elapsed=round((time.perf_counter()-started)*1000)
        assert job.tasks_created==6 and job.analysis_failures==0
        return {'concurrency':concurrency,'elapsed_ms':elapsed,'tasks_created':job.tasks_created}


if __name__=='__main__':
    print(json.dumps({'workload':'6 synthetic emails; 100ms mocked provider delay per analysis; SQLite; no network',
                      'sequential':measure(1),'bounded':measure(2)}))
