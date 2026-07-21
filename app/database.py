import os

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker


DEFAULT_DATABASE_URL = "sqlite:///./actioninbox.db"


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str | None = None) -> Engine:
    url = make_url(database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))
    options: dict = {"pool_pre_ping": True}
    if url.get_backend_name() == "sqlite":
        options["connect_args"] = {"check_same_thread": False}
    configured_engine = create_engine(url, **options)
    if url.get_backend_name() == "sqlite":
        @event.listens_for(configured_engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return configured_engine


DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
engine = build_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    with SessionLocal() as session:
        yield session
