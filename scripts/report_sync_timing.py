"""Read-only aggregate timings. Never print identifiers, values, or credentials."""
import json
import statistics
from sqlalchemy import text, inspect
from app.database import SessionLocal

with SessionLocal() as db:
    columns={c['name'] for c in inspect(db.get_bind()).get_columns('gmail_sync_jobs')}
    timed='triage_ms' in columns
    fields='created_at,started_at,completed_at,imported'
    if timed:
        fields+=',fetch_ms,storage_ms,triage_ms,queue_wait_ms'
    rows=db.execute(text('SELECT '+fields+' FROM gmail_sync_jobs WHERE completed_at IS NOT NULL AND attempts=1 ORDER BY id DESC LIMIT 30')).mappings().all()
    def median(values):
        return round(statistics.median(values),3) if values else None
    output={'sample_jobs':len(rows),'queue_seconds_median':median([(r['started_at']-r['created_at']).total_seconds() for r in rows if r['started_at']]),
            'worker_seconds_median':median([(r['completed_at']-r['started_at']).total_seconds() for r in rows if r['started_at']]),
            'stage_instrumentation_available':timed}
    if timed:
        # Only jobs that passed through the new instrumentation, not historical zero defaults.
        rows=[r for r in rows if r['fetch_ms'] or r['storage_ms'] or r['triage_ms']]
        output['instrumented_jobs']=len(rows)
        for field in ('fetch_ms','storage_ms','triage_ms','queue_wait_ms'):
            output[field+'_median']=median([r[field] for r in rows])
    print('SYNC_TIMING_AGGREGATE '+json.dumps(output))
