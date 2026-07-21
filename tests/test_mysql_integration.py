import os
from uuid import uuid4

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal, engine
from app.demo_data import DEMO_EMAILS
from app.models import Email, Task, User


@pytest.mark.skipif(os.getenv("RUN_MYSQL_INTEGRATION") != "true", reason="requires the Compose MySQL service")
def test_mysql_migration_and_cross_user_constraint():
    assert engine.dialect.name == "mysql"
    assert {"users", "emails", "analyses", "tasks", "business_resources", "gmail_credentials"} <= set(inspect(engine).get_table_names())
    suffix = uuid4().hex
    user_a = User(id=str(uuid4()), email=f"mysql-a-{suffix}@example.test", display_name="MySQL A")
    user_b = User(id=str(uuid4()), email=f"mysql-b-{suffix}@example.test", display_name="MySQL B")
    with SessionLocal() as db:
        db.add_all([user_a, user_b])
        db.commit()
        email_data = {**DEMO_EMAILS[0], "external_id": f"mysql-{suffix}"}
        email = Email(**email_data, source="test", user_id=user_a.id)
        db.add(email)
        db.commit()
        db.add(Task(user_id=user_b.id, email_id=email.id, title="Forbidden cross-owner task"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.delete(user_a)
        db.delete(user_b)
        db.commit()
