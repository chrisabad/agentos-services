"""SQLAlchemy ORM models and Pydantic schemas for the Notification Service."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import DateTime, JSON, String, Text, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class _JSONB(TypeDecorator):
    """JSON that renders as JSONB on PostgreSQL and plain JSON on SQLite/others."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import JSONB
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class Base(DeclarativeBase):
    pass


PriorityT = Literal["immediate", "daily_brief", "weekly_brief", "muted"]
StateT = Literal["new", "read", "acted", "escalated", "suppressed"]

_VALID_TRANSITIONS: dict[str, set[str]] = {
    "new": {"read", "acted", "escalated", "suppressed"},
    "read": {"acted", "escalated", "suppressed"},
    "acted": set(),
    "escalated": set(),
    "suppressed": set(),
}


def valid_transition(current: str, next_state: str) -> bool:
    return next_state in _VALID_TRANSITIONS.get(current, set())


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    topic_class: Mapped[str] = mapped_column(String(120), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="immediate")
    payload: Mapped[dict] = mapped_column(_JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    feedback: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    juno_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    juno_acted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    chris_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalated_slack_ts: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class NotificationCreate(BaseModel):
    source: str = Field(min_length=1)
    topic_class: str = Field(min_length=1)
    priority: PriorityT = "immediate"
    payload: dict[str, Any] = Field(default_factory=dict)
    fingerprint: str = Field(min_length=1)
    dedup_window_hours: int = Field(default=24, ge=0, le=8760)


class NotificationRead(BaseModel):
    id: uuid.UUID
    source: str
    topic_class: str
    priority: str
    payload: dict[str, Any]
    state: str
    feedback: dict[str, Any] | None
    fingerprint: str
    juno_seen_at: datetime | None
    juno_acted_at: datetime | None
    chris_seen_at: datetime | None
    escalated_slack_ts: str | None
    created_at: datetime
    updated_at: datetime
    duplicate: bool = False

    model_config = {"from_attributes": True}


class NotificationPatch(BaseModel):
    state: StateT
    juno_seen_at: datetime | None = None
    juno_acted_at: datetime | None = None
    chris_seen_at: datetime | None = None
    escalated_slack_ts: str | None = None


class FeedbackWrite(BaseModel):
    sentiment: Literal["positive", "negative"]
    reason: str | None = None
    notes: str | None = None
    reactions: list[str] = Field(default_factory=list)
