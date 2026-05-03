"""Pydantic schemas for the Memory Service endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    source: str = Field(description="Origin path or graph node identifier")
    score: float = Field(description="Aggregate retrieval score (0..1, higher is better)")
    excerpt: str = Field(description="Matching text or summary")
    kind: Literal["memory_md", "graphiti", "session"] = Field(
        description="Where this result came from"
    )
    ts: Optional[datetime] = Field(default=None, description="Original write time, if known")


class SearchResponse(BaseModel):
    results: list[SearchResult]
    query: str
    agent: str


class AppendRequest(BaseModel):
    agent: str = Field(description="Agent name (matches the directory under workspace/agents/)")
    text: str = Field(min_length=1, description="Memory text to record")
    kind: str = Field(default="manual", description="Origin tag (manual, dreaming, etc.)")
    source: Optional[str] = Field(
        default=None, description="Optional provenance path or comment URL"
    )


class AppendResponse(BaseModel):
    memory_id: str
    agent: str
    written_path: str


class PromoteRequest(BaseModel):
    agent: str
    text: str = Field(min_length=1, description="Promoted memory text")
    source: str | None = Field(default=None, description="Origin candidate path / id")
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class PromoteResponse(BaseModel):
    memory_id: str
    agent: str
    written_path: str
    graphiti_node_uuid: str | None = Field(
        default=None, description="UUID of the created Graphiti Assessment node, if Graphiti is reachable"
    )
