import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./actioninbox.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

def get_db():
    with SessionLocal() as session:
        yield session

def initialize_database():
    Base.metadata.create_all(engine)
    if engine.dialect.name != "sqlite":
        return
    columns = {column["name"] for column in inspect(engine).get_columns("analyses")}
    additions = {
        "structured_result": "TEXT",
        "source": "VARCHAR(30) NOT NULL DEFAULT 'demo_fallback'",
        "model": "VARCHAR(100)",
        "error_message": "TEXT",
    }
    with engine.begin() as connection:
        for name, sql_type in additions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE analyses ADD COLUMN {name} {sql_type}"))
