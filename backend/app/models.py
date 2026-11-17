"""Database models: users, projects, tasks, agent messages, events, approvals, deliverables, usage."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from . import config

_kwargs = {"connect_args": {"check_same_thread": False}} if config.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(config.DATABASE_URL, **_kwargs)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def uid() -> str:
    return uuid.uuid4().hex


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="Untitled project")
    objective: Mapped[str] = mapped_column(Text)
    # planning | awaiting_plan_approval | running | paused | awaiting_approval | completed | failed | cancelled
    status: Mapped[str] = mapped_column(String(32), default="planning")
    summary: Mapped[str] = mapped_column(Text, default="")
    questions: Mapped[list] = mapped_column(JSON, default=list)
    assumptions: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[list] = mapped_column(JSON, default=list)  # shared project context added by the user
    review_feedback: Mapped[str] = mapped_column(Text, default="")
    budget_usd: Mapped[float] = mapped_column(Float, default=config.DEFAULT_BUDGET_USD)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    key: Mapped[str] = mapped_column(String(16))
    position: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(300))
    instructions: Mapped[str] = mapped_column(Text, default="")
    acceptance_criteria: Mapped[str] = mapped_column(Text, default="")
    agent: Mapped[str] = mapped_column(String(32))
    depends_on: Mapped[list] = mapped_column(JSON, default=list)
    priority: Mapped[str] = mapped_column(String(16), default="medium")
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deliverable: Mapped[bool] = mapped_column(Boolean, default=False)
    # pending | running | in_review | needs_revision | awaiting_approval | completed | failed | skipped | cancelled
    status: Mapped[str] = mapped_column(String(32), default="pending")
    output: Mapped[str] = mapped_column(Text, default="")
    qa_feedback: Mapped[str] = mapped_column(Text, default="")
    qa_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    history: Mapped[list] = mapped_column(JSON, default=list)  # per-attempt outcomes
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Message(Base):
    """Structured agent-to-agent message (PRD 8.3)."""
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sender: Mapped[str] = mapped_column(String(32))
    recipient: Mapped[str] = mapped_column(String(32))
    type: Mapped[str] = mapped_column(String(32))  # assignment | result | review | revision_request | escalation | delivery
    content: Mapped[str] = mapped_column(Text, default="")
    output_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="delivered")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Event(Base):
    """Execution history / timeline entry."""
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(48))
    agent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))  # plan | action | escalation | budget | deliverable
    title: Mapped[str] = mapped_column(String(300))
    details: Mapped[str] = mapped_column(Text, default="")
    approve_label: Mapped[str] = mapped_column(String(60), default="Approve")
    reject_label: Mapped[str] = mapped_column(String(60), default="Reject")
    on_approve: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | approved | rejected
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Deliverable(Base):
    __tablename__ = "deliverables"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))  # final_report | supporting
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text, default="")
    qa_notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | approved | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Usage(Base):
    __tablename__ = "usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    agent: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


def init_db() -> None:
    Base.metadata.create_all(engine)
