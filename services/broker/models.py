"""Pydantic schemas for the Attention Broker HTTP endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BrokerCheckRequest(BaseModel):
    service: str = Field(min_length=1, description="Service / business context (age, fon, kaleidoscope, ...)")
    problem_type: str = Field(min_length=1, description="Problem category (oauth_expired, build_failure, ...)")
    resource: str = Field(min_length=1, description="Specific resource affected")
    canonical_name: str = Field(default="", description="Human-readable topic name")
    flow: Literal["juno_to_chris", "agent_to_juno"] = "juno_to_chris"
    consumer: Literal["chris", "juno"] = "chris"
    business: str = Field(default="", description="Business context (defaults to service)")
    category: str = Field(default="ops", description="Topic category (ops, financial, approval, ...)")
    related_issue_ids: list[str] = Field(default_factory=list)
    context: dict = Field(default_factory=dict)
    surface_tier: Literal["immediate", "daily_brief", "weekly_brief", "muted"] = "immediate"
    agent_id: str | None = Field(default=None, description="Agent ID for topic-level cooldown tracking (optional)")
    dry_run: bool = False


class BrokerCheckResponse(BaseModel):
    decision: Literal["surface", "suppress", "batch", "decay"]
    reason: str
    rule_id: str
    fingerprint: str
    resolved_channel: str | None
    topic_state: str
    topic_tier: str

    @property
    def suppressed(self) -> bool:
        return self.decision == "suppress"

    @property
    def should_surface(self) -> bool:
        return self.decision == "surface"

    @property
    def should_batch(self) -> bool:
        return self.decision == "batch"


class TopicLookupRequest(BaseModel):
    service: str = Field(min_length=1)
    problem_type: str = Field(min_length=1)
    resource: str = Field(min_length=1)


class RecordActionRequest(BaseModel):
    service: str = Field(min_length=1)
    problem_type: str = Field(min_length=1)
    resource: str = Field(min_length=1)
    action: str = Field(min_length=1, description="Action taken (comment_posted, issue_closed, messaged_chris, ...)")
    evidence_ref: str = Field(default="", description="Optional evidence pointer (issue id, comment id, slack ts)")


class DispositionRequest(BaseModel):
    service: str = Field(min_length=1)
    problem_type: str = Field(min_length=1)
    resource: str = Field(min_length=1)
    disposition: Literal["acknowledged", "resolved", "muted"]
    source: str = Field(default="explicit", description="Where the disposition signal came from")
    evidence: str = Field(default="")
    muted_until: str | None = Field(default=None, description="ISO8601 expiry for mutes (optional)")


class SimpleAck(BaseModel):
    ok: bool
    fingerprint: str
    detail: str = ""


class TopicSummary(BaseModel):
    fingerprint: str
    canonical_name: str
    state: str
    surface_tier: str
    surface_count: int
    last_surfaced: str | None = None
    disposition: str | None = None
    related_issue_ids: list[str] = Field(default_factory=list)
    producer_actions: list[dict] = Field(default_factory=list)
    resolved_channel: str | None = None


class StandingDecisionsResponse(BaseModel):
    rules: list[dict]


class StatsResponse(BaseModel):
    total_topics: int
    by_state: dict
    by_tier: dict
    total_surfaces: int
    version: int
