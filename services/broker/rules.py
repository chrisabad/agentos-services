"""Attention Broker rule engine.

Implements the deterministic rule layer for the 7-step decision flow. Each rule
is a small function `(topic, context) -> tuple[decision, reason] | None`. The
engine evaluates rules in priority order; the first rule that returns a non-None
verdict wins. Rules that return None pass; if all rules pass, the default is
SURFACE.

Rule numbering follows the PRD amendment (2026-04-23). This module ships the
critical-path subset (R20, R23, R24, R25, R12/R19, R18, R28, plus default
SURFACE). Remaining rules R01-R39 are tracked under follow-up issue Phase 1.1b.

Channel resolution (`resolve_channel`) and tier decay (`decay_tier`) are
also kept here since they're rule-adjacent and used directly by broker.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

# ── Decision constants ───────────────────────────────────────────────
DECISION_SUPPRESS = "suppress"
DECISION_SURFACE = "surface"
DECISION_BATCH = "batch"
DECISION_DECAY = "decay"

VALID_DECISIONS = {DECISION_SUPPRESS, DECISION_SURFACE, DECISION_BATCH, DECISION_DECAY}

# ── Tier decay chain ─────────────────────────────────────────────────
TIER_ORDER = ["immediate", "daily_brief", "weekly_brief", "muted"]


def decay_tier(current: str) -> str:
    """Walk one step down the tier chain. Idempotent at 'muted'."""
    if current not in TIER_ORDER:
        return "muted"
    idx = TIER_ORDER.index(current)
    return TIER_ORDER[min(idx + 1, len(TIER_ORDER) - 1)]


# ── Channel resolution ───────────────────────────────────────────────
# Mapping of (business, category) → preferred channel. Falls back to a generic
# default per business + finally a global default. The original kaleidoscope-
# policy step 9 hard-coded a few overrides (e.g. R14 → #agent-ops); broker.py
# preserves the override path for those cases.

_CHANNEL_BY_BUSINESS_CATEGORY: dict[tuple[str, str], str] = {
    # ops alerts go to #agent-ops; financial to #finance; approvals to DM
    ("age", "ops"): "C0AKKLWGNG4",      # #agent-ops
    ("age", "approval"): "DM:chris",
    ("age", "financial"): "C0AGENTFIN1",
    ("kaleidoscope", "ops"): "C0AGENTOPS",
    ("kaleidoscope", "financial"): "C0AGENTFIN1",
    ("font_replacer", "financial"): "C0AGENTFIN1",
    ("weekend", "ops"): "C0AGENTOPS",
}

_DEFAULT_CHANNEL_BY_BUSINESS: dict[str, str] = {
    "age": "DM:chris",
    "kaleidoscope": "DM:chris",
    "font_replacer": "DM:chris",
    "weekend": "DM:chris",
}

_GLOBAL_DEFAULT_CHANNEL = "DM:chris"


def resolve_channel(business: str, category: str, surface_tier: str) -> str:
    """Return the channel to deliver to. Briefs route to brief-specific channels."""
    business_norm = (business or "").lower()
    category_norm = (category or "").lower()
    if surface_tier == "daily_brief":
        return f"BRIEF:daily:{business_norm or 'general'}"
    if surface_tier == "weekly_brief":
        return f"BRIEF:weekly:{business_norm or 'general'}"
    if surface_tier == "muted":
        return "MUTED"
    return (
        _CHANNEL_BY_BUSINESS_CATEGORY.get((business_norm, category_norm))
        or _DEFAULT_CHANNEL_BY_BUSINESS.get(business_norm)
        or _GLOBAL_DEFAULT_CHANNEL
    )


# ── Rule helpers ─────────────────────────────────────────────────────


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        v = value[:-1] + "+00:00" if value.endswith("Z") else value
        return datetime.fromisoformat(v)
    except (ValueError, AttributeError):
        return None


def _hours_since(value: str | None) -> float | None:
    dt = _parse_iso(value)
    if not dt:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


# ── Rule definitions ─────────────────────────────────────────────────
# Each rule returns (decision, reason) | None (no verdict).


def r18_all_clear_messages(topic: dict, ctx: dict) -> tuple[str, str] | None:
    """R18: all-clear / heartbeat-OK / status-green messages → suppress."""
    text = (ctx.get("message_text") or topic.get("canonical_name") or "").lower()
    markers = ("all clear", "all-clear", "all good", "heartbeat ok", "status: green", "no anomalies")
    if any(m in text for m in markers):
        return DECISION_SUPPRESS, "All-clear / status-green message"
    return None


def r20_producer_already_acted(topic: dict, ctx: dict) -> tuple[str, str] | None:
    """R20: in agent_to_juno flow, suppress if Juno has already taken action on this topic."""
    if ctx.get("flow") != "agent_to_juno":
        return None
    actions = topic.get("producer_actions") or []
    if actions:
        latest = actions[-1]
        return DECISION_SUPPRESS, f"Producer already acted ({latest.get('action','?')})"
    return None


def r23_juno_already_acted(topic: dict, ctx: dict) -> tuple[str, str] | None:
    """R23: in juno_to_chris flow, suppress if Juno has already messaged on this topic."""
    if ctx.get("flow") != "juno_to_chris":
        return None
    actions = topic.get("producer_actions") or []
    surface_count = int(topic.get("surface_count") or 0)
    if surface_count > 0 and actions:
        return DECISION_SUPPRESS, "Juno already surfaced this topic"
    return None


def r24_already_acknowledged_or_resolved(topic: dict, ctx: dict) -> tuple[str, str] | None:
    """R24: topic already acknowledged or resolved → suppress."""
    state = topic.get("state")
    if state in ("acknowledged", "resolved"):
        return DECISION_SUPPRESS, f"Topic already {state}"
    return None


def r25_muted(topic: dict, ctx: dict) -> tuple[str, str] | None:
    """R25: topic muted → suppress (unless mute expiry has passed)."""
    if topic.get("state") != "muted":
        return None
    until = topic.get("muted_until")
    if until:
        until_dt = _parse_iso(until)
        if until_dt and datetime.now(timezone.utc) >= until_dt:
            # Expiry passed — let other rules run
            return None
    return DECISION_SUPPRESS, "Topic is muted"


def r19_recently_surfaced(topic: dict, ctx: dict) -> tuple[str, str] | None:
    """R19/R12: if surfaced within last 12h, suppress to avoid spamming."""
    last = topic.get("last_surfaced")
    hours = _hours_since(last)
    if hours is not None and hours < 12.0:
        return DECISION_SUPPRESS, f"Recently surfaced ({hours:.1f}h ago)"
    return None


def r16_decay_no_response(topic: dict, ctx: dict) -> tuple[str, str] | None:
    """R16: 2+ surfaces with no response → decay one tier."""
    surface_count = int(topic.get("surface_count") or 0)
    if surface_count < 2:
        return None
    if topic.get("state") in ("acknowledged", "resolved", "muted"):
        return None
    if topic.get("disposition") in ("acknowledged", "resolved"):
        return None
    return DECISION_DECAY, f"No response after {surface_count} surfaces — decaying tier"


def r28_betterstack_non_critical(topic: dict, ctx: dict) -> tuple[str, str] | None:
    """R28: BetterStack non-critical alerts go to daily brief."""
    if (ctx.get("source") or "").lower() != "betterstack":
        return None
    severity = (ctx.get("severity") or "").lower()
    if severity in ("warning", "info", "low"):
        return DECISION_BATCH, "BetterStack non-critical → daily brief"
    return None


# Default fallthrough: surface
def _default_surface(topic: dict, ctx: dict) -> tuple[str, str]:
    return DECISION_SURFACE, "No suppression rule matched"


# Rule registry: ordered list of (rule_id, callable)
DEFAULT_RULES: list[tuple[str, Callable[[dict, dict], tuple[str, str] | None]]] = [
    ("R18", r18_all_clear_messages),
    ("R20", r20_producer_already_acted),
    ("R23", r23_juno_already_acted),
    ("R24", r24_already_acknowledged_or_resolved),
    ("R25", r25_muted),
    ("R19", r19_recently_surfaced),
    ("R16", r16_decay_no_response),
    ("R28", r28_betterstack_non_critical),
]


@dataclass
class RuleEngine:
    """Evaluates rules in priority order, returning (decision, reason, rule_id)."""

    rules: list[tuple[str, Callable[[dict, dict], tuple[str, str] | None]]] = field(
        default_factory=lambda: list(DEFAULT_RULES)
    )

    def evaluate(self, topic: dict, ctx: dict) -> tuple[str, str, str]:
        for rule_id, fn in self.rules:
            try:
                verdict = fn(topic, ctx)
            except Exception:  # rule errors should not block delivery
                continue  # noqa: E701
            if verdict is None:
                continue
            decision, reason = verdict
            if decision not in VALID_DECISIONS:
                continue
            return decision, reason, rule_id
        decision, reason = _default_surface(topic, ctx)
        return decision, reason, "DEFAULT"


def load_rules(_path: str | None = None) -> list[tuple[str, Callable[[dict, dict], Any]]]:
    """Hook for loading rule overrides from a YAML file. Phase 1.1 returns DEFAULT_RULES.

    Phase 1.1b will read from `~/.agentos/state/broker/rules.yaml` and allow
    standing-decision rules to be expressed declaratively.
    """
    return list(DEFAULT_RULES)
