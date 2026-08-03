import logging
import time

from .agent_execution import process_next_execution
from .database import SessionLocal


logging.basicConfig(level=logging.INFO)


def main():
    while True:
        with SessionLocal() as db:
            processed = process_next_execution(db)
        if processed is None:
            time.sleep(2)


if __name__ == "__main__":
    main()
