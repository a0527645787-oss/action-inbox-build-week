import logging
import os
import time

from .database import SessionLocal
from .gmail import claim_gmail_sync_job, run_gmail_sync_job


logger = logging.getLogger(__name__)


def run_forever() -> None:
    poll_seconds = max(float(os.getenv("GMAIL_SYNC_WORKER_POLL_SECONDS", "2")), 0.25)
    while True:
        try:
            with SessionLocal() as db:
                job = claim_gmail_sync_job(db)
                if job is None:
                    time.sleep(poll_seconds)
                    continue
                run_gmail_sync_job(db, job)
        except Exception as exc:
            logger.error("Gmail sync worker loop recovered exception_class=%s", type(exc).__name__)
            time.sleep(poll_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    run_forever()
