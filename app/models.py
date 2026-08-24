from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    google_subject: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Email(Base):
    __tablename__ = "emails"
    __table_args__ = (
        UniqueConstraint("user_id", "external_id", name="uq_emails_user_external_id"),
        UniqueConstraint("user_id", "id", name="uq_emails_user_id_id"),
        UniqueConstraint("user_id", "gmail_message_id", name="uq_emails_user_gmail_message"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(100))
    sender: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(255))
    received_at: Mapped[datetime] = mapped_column(DateTime)
    body: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(30), default="demo")
    gmail_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    analyzed: Mapped[bool] = mapped_column(Boolean, default=False)
    analysis: Mapped["Analysis | None"] = relationship(back_populates="email", cascade="all, delete-orphan", uselist=False)
    task: Mapped["Task | None"] = relationship(back_populates="email", cascade="all, delete-orphan", uselist=False)


class Analysis(Base):
    __tablename__ = "analyses"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "email_id"], ["emails.user_id", "emails.id"], ondelete="CASCADE", name="fk_analyses_user_email"),
        UniqueConstraint("user_id", "email_id", name="uq_analyses_user_email"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    email_id: Mapped[int] = mapped_column(Integer)
    classification: Mapped[str] = mapped_column(String(40))
    action_required: Mapped[bool] = mapped_column(Boolean)
    summary: Mapped[str] = mapped_column(Text)
    evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(30), default="demo_fallback")
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    email: Mapped[Email] = relationship(back_populates="analysis")


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "email_id"], ["emails.user_id", "emails.id"], ondelete="CASCADE", name="fk_tasks_user_email"),
        UniqueConstraint("user_id", "email_id", name="uq_tasks_user_email"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    email_id: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deadline_text: Mapped[str | None] = mapped_column(String(100), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    email: Mapped[Email] = relationship(back_populates="task")
    executions: Mapped[list["Execution"]] = relationship(back_populates="task", cascade="all, delete-orphan")
    sheet_append_record: Mapped["SheetAppendRecord | None"] = relationship(back_populates="task", cascade="all, delete-orphan", uselist=False)


class Execution(Base):
    __tablename__ = "executions"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_executions_user_idempotency"),
        UniqueConstraint("user_id", "task_id", "sheet_proposal_slot", name="uq_executions_sheet_task"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="awaiting_approval", index=True)
    plan: Mapped[str] = mapped_column(Text)
    plan_hash: Mapped[str] = mapped_column(String(64))
    tool_name: Mapped[str] = mapped_column(String(100))
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    sheet_proposal_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    task: Mapped[Task] = relationship(back_populates="executions")
    events: Mapped[list["ExecutionEvent"]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="ExecutionEvent.id",
    )


class ExecutionEvent(Base):
    __tablename__ = "execution_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(Text)
    safe_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    execution: Mapped[Execution] = relationship(back_populates="events")


class BusinessResource(Base):
    __tablename__ = "business_resources"
    __table_args__ = (UniqueConstraint("user_id", "title", name="uq_resources_user_title"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    resource_type: Mapped[str] = mapped_column(String(60))
    content: Mapped[str] = mapped_column(Text)
    organization_team: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class GmailCredential(Base):
    __tablename__ = "gmail_credentials"
    __table_args__ = (UniqueConstraint("user_id", "account_email", name="uq_gmail_user_account"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_email: Mapped[str] = mapped_column(String(320))
    encrypted_token: Mapped[str] = mapped_column(Text)
    scopes: Mapped[str] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    history_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bootstrap_page_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class GmailSyncJob(Base):
    __tablename__ = "gmail_sync_jobs"
    __table_args__ = (
        UniqueConstraint("credential_id", "active_slot", name="uq_gmail_sync_active_credential"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    credential_id: Mapped[int] = mapped_column(ForeignKey("gmail_credentials.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    active_slot: Mapped[int | None] = mapped_column(Integer, default=1, nullable=True)
    mode: Mapped[str] = mapped_column(String(30), default="bootstrap")
    page_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_history_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pending_history_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pages_listed: Mapped[int] = mapped_column(Integer, default=0)
    candidates: Mapped[int] = mapped_column(Integer, default=0)
    details_fetched: Mapped[int] = mapped_column(Integer, default=0)
    imported: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    safe_error: Mapped[str | None] = mapped_column(String(80), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SheetAppendRecord(Base):
    __tablename__ = "sheet_append_records"
    __table_args__ = (
        UniqueConstraint("proposal_id", name="uq_sheet_append_proposal"),
        UniqueConstraint("user_id", "task_id", name="uq_sheet_append_user_task"),
        UniqueConstraint("execution_id", name="uq_sheet_append_execution"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[str] = mapped_column(String(80))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"), index=True)
    spreadsheet_id: Mapped[str] = mapped_column(String(255))
    sheet_tab: Mapped[str] = mapped_column(String(255))
    sheet_range: Mapped[str] = mapped_column(String(255))
    row_hash: Mapped[str] = mapped_column(String(64))
    verification_status: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    task: Mapped[Task] = relationship(back_populates="sheet_append_record")


class GmailOAuthState(Base):
    __tablename__ = "gmail_oauth_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
