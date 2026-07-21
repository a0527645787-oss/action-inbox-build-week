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
    email: Mapped[Email] = relationship(back_populates="task")


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class GmailOAuthState(Base):
    __tablename__ = "gmail_oauth_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
