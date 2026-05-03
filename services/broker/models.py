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
