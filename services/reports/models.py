"""SQLAlchemy ORM models and Pydantic schemas for the Reports Service."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import DateTime, JSON, String, Text, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class _JSONB(TypeDecorator):
    """JSON that renders as JSONB on PostgreSQL and plain JSON on SQLite/others."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import JSONB
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


StateT = Literal["drafted", "reviewed", "published", "archived"]
StorageT = Literal["notion", "paperclip_doc"]

_VALID_TRANSITIONS: dict[str, set[str]] = {
    "drafted": {"reviewed", "archived"},
    "reviewed": {"published", "drafted"},
    "published": {"archived"},
    "archived": set(),
}


def valid_transition(current: str, next_state: str) -> bool:
    return next_state in _VALID_TRANSITIONS.get(current, set())


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    topic_class: Mapped[str] = mapped_column(String(120), nullable=False)
    draft_version: Mapped[int] = mapped_column(nullable=False, default=1)
    published_version: Mapped[int | None] = mapped_column(nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="drafted")
    storage_type: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_doc_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    juno_review: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)
    sources_cited: Mapped[list] = mapped_column(_JSONB, nullable=False, default=list)
    feedback: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ReportCreate(BaseModel):
    source: str = Field(min_length=1)
    topic_class: str = Field(min_length=1)
    storage_type: StorageT
    storage_url: str | None = None
    storage_doc_id: str | None = None
    sources_cited: list[dict[str, Any]] = Field(default_factory=list)


class ReportRead(BaseModel):
    id: uuid.UUID
    source: str
    topic_class: str
    draft_version: int
    published_version: int | None
    state: str
    storage_type: str
    storage_url: str | None
    storage_doc_id: str | None
    juno_review: dict[str, Any] | None
    sources_cited: list[dict[str, Any]]
    feedback: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None

    model_config = {"from_attributes": True}


class ReportPatch(BaseModel):
    state: StateT
    storage_url: str | None = None
    storage_doc_id: str | None = None


class JunoReviewWrite(BaseModel):
    reviewed_by: str = Field(min_length=1)
    edits_summary: str | None = None
    kicked_back_to: str | None = None


class FeedbackWrite(BaseModel):
    sentiment: Literal["positive", "negative"]
    reason: str | None = None
    notes: str | None = None
    reactions: list[str] = Field(default_factory=list)
