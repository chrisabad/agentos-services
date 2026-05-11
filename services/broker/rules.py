"""Attention Broker rule engine.

Implements the deterministic rule layer for the 7-step decision flow. Each rule
is a small function `(topic, context) -> tuple[decision, reason] | None`. The
engine evaluates rules in priority order; the first rule that returns a non-None
verdict wins. Rules that return None pass; if all rules pass, the default is
SURFACE.

The original PRD amendment (2026-04-23) referenced a 39-rule taxonomy whose
spec was not preserved on disk. The current rule set is grounded in two
sources: the 8 critical-path rules ported in Phase 1.1, and the 4 hard-block
content rules migrated from `kaleidoscope-policy/index.js` steps 1-4 (R29-R32,
Phase 1.1b — see AGE-12132).

Channel routing includes a `personal` business lane: all personal categories
(benefits, health, finance, household, and any unknown) route to #general
(C0GENERAL). This ensures personal-scope notifications land in a wide-audience
channel rather than defaulting to DM:chris (AGE-13645).

Channel resolution (`resolve_channel`) and tier decay (`decay_tier`) are
also kept here since they're rule-adjacent and used directly by broker.py.
"""

from __future__ import annotations

import re
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
    # ops alerts go to #agent-ops; financial to #finance; approvals routed
    # explicitly. NO DM defaults — DM routing requires an explicit mapping.
    ("age", "ops"): "C0AKKLWGNG4",      # #agent-ops
    ("age", "approval"): "DM:chris",    # explicit DM for approval flow only
    ("age", "financial"): "C0AGENTFIN1",
    ("kaleidoscope", "ops"): "C0AGENTOPS",
    ("kaleidoscope", "financial"): "C0AGENTFIN1",
    ("font_replacer", "ops"): "C0AKKLWGNG4",      # AGE-13744 fix — was falling through to DM:chris
    ("font_replacer", "financial"): "C0AGENTFIN1",
    ("weekend", "ops"): "C0AGENTOPS",
    # personal — all categories route to #general
    ("personal", "benefits"): "C0GENERAL",    # #general
    ("personal", "health"): "C0GENERAL",
    ("personal", "finance"): "C0GENERAL",
    ("personal", "household"): "C0GENERAL",
}

# Per-business fallback channels — used when no (business, category) mapping
# matches. Explicitly NOT DM:chris — routing to a Chris DM requires the
# explicit (business, category) → DM:chris mapping above. This avoids the
# AGE-13744 regression where Font Replacer ops alerts silently fell through
# to Chris's DM.
_DEFAULT_CHANNEL_BY_BUSINESS: dict[str, str] = {
    "age": "C0AKKLWGNG4",           # #agent-ops
    "kaleidoscope": "C0AGENTOPS",   # #agent-ops (kaleidoscope workspace)
    "font_replacer": "C0AKKLWGNG4", # #agent-ops
    "weekend": "C0AGENTOPS",
    "personal": "C0GENERAL",        # #general — catch-all for personal categories
}

# Business name aliases — producers may pass shortened (e.g., "fon") or
# hyphenated (e.g., "font-replacer") variants. Normalize to canonical form
# before lookup. Underscore separator collapse happens in resolve_channel.
_BUSINESS_ALIASES: dict[str, str] = {
    "fon": "font_replacer",
    "kal": "kaleidoscope",
    "wee": "weekend",
    "pix": "pixelated_path",
    "stu": "studio",
    "dia": "diacritic_mining",
}

# Unknown businesses fall back to #agent-ops, NOT to a Chris DM.
_GLOBAL_DEFAULT_CHANNEL = "C0AKKLWGNG4"  # #agent-ops


def _normalize_business(business: str) -> str:
    """Normalize a business identifier: lowercase, collapse hyphens to underscores,
    apply alias map. So 'font-replacer', 'font_replacer', 'Font Replacer', and 'fon'
    all collapse to 'font_replacer'."""
    if not business:
        return ""
    norm = business.lower().strip().replace("-", "_").replace(" ", "_")
    return _BUSINESS_ALIASES.get(norm, norm)


def resolve_channel(business: str, category: str, surface_tier: str) -> str:
    """Return the channel to deliver to. Briefs route to brief-specific channels.

    Business name normalization: hyphens/spaces collapse to underscores, and known
    short forms (e.g., 'fon' → 'font_replacer') are resolved before lookup.
    """
    business_norm = _normalize_business(business)
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


# ── Policy-migration rules (R29-R32) ─────────────────────────────────
# Migrated from kaleidoscope-policy/index.js steps 1-4 (AGE-12132). These rules
# inspect message_text in the context dict; when the context has no
# `message_text`, the rule passes (returns None).

_RAW_ERROR_PATTERNS = (
    re.compile(r"Error:\s+\w"),
    re.compile(r"HTTP Error \d{3}"),
    re.compile(r"ENOENT:"),
    re.compile(r"Connection refused"),
    re.compile(r"API route not found"),
    re.compile(r"Channel is unavailable"),
    re.compile(r"Traceback \(most recent call last\)"),
    re.compile(r"SyntaxError:"),
    re.compile(r"TypeError:"),
    re.compile(r"^\s*at\s+\w.*:\d+:\d+", re.MULTILINE),
)

_BARE_TICKET_RE = re.compile(r"^[\s\-•*]*([A-Z]+-\d+[\s,;]*){1,3}[\s.]*$")
_CONTENT_DRAFT_CTA = re.compile(
    r"studiomethod\.ai|substack\.com|follow me|subscribe|link in bio",
    re.IGNORECASE,
)
_HASHTAG_RE = re.compile(r"#\w+")


def _is_raw_error(text: str) -> bool:
    return any(p.search(text) for p in _RAW_ERROR_PATTERNS)


def _has_bare_ticket_id(text: str) -> bool:
    return bool(_BARE_TICKET_RE.match(text.strip()))


def _is_content_draft(text: str) -> bool:
    if not text or len(text) < 200:
        return False
    if text.count("\n") < 4:
        return False
    has_cta = bool(_CONTENT_DRAFT_CTA.search(text))
    has_hashtags = len(_HASHTAG_RE.findall(text)) >= 2
    return has_cta and has_hashtags


def simple_hash(text: str) -> str:
    """DJB2-style 32-bit hash. Matches the kaleidoscope-policy plugin's
    simpleHash so dedup state is comparable across the JS/Python boundary
    while both implementations run in parallel."""
    h = 0
    for c in text[:200]:
        h = ((h << 5) - h + ord(c)) & 0xFFFFFFFF
        if h & 0x80000000:
            h -= 0x100000000
    return str(h)


def r29_raw_error(topic: dict, ctx: dict) -> tuple[str, str] | None:
    """R29: outbound message contains a raw error / stack trace → suppress.

    Migrated from kaleidoscope-policy step 1 (block_raw_errors_to_slack).
    """
    text = ctx.get("message_text") or ""
    if text and _is_raw_error(text):
        return DECISION_SUPPRESS, "Raw error / stack trace in outbound message"
    return None


def r30_bare_ticket_id(topic: dict, ctx: dict) -> tuple[str, str] | None:
    """R30: message body is only a bare ticket ID with no context → suppress.

    Migrated from kaleidoscope-policy step 2 (block_bare_ticket_ids).
    """
    text = ctx.get("message_text") or ""
    if text and _has_bare_ticket_id(text):
        return DECISION_SUPPRESS, "Bare ticket ID without context"
    return None


def r31_content_draft(topic: dict, ctx: dict) -> tuple[str, str] | None:
    """R31: long-form content draft (newsletter / LinkedIn) routed to chat → suppress.

    Migrated from kaleidoscope-policy step 3 (block_content_drafts_to_chat).
    Conservative heuristic — must have CTA marker AND >=2 hashtags AND
    >=200 chars AND >=4 newlines.
    """
    text = ctx.get("message_text") or ""
    if text and _is_content_draft(text):
        return DECISION_SUPPRESS, "Content draft suppressed — route through Content Studio"
    return None


# Slack channels that get the 24h dedup treatment (Chris's high-priority targets)
CHRIS_CHANNELS = frozenset(["C0AKKLWGNG4", "D0AFURXGVTM"])


def normalize_for_dedup(text: str) -> str:
    """Strip ISO timestamps + collapse whitespace + lowercase + truncate to 200 chars.

    Mirrors the kaleidoscope-policy `normalizeForDedup` so the same message
    posted at 09:00 and 14:00 is recognized as a duplicate (timestamp drift
    is the only thing that changed).
    """
    text = re.sub(r"\[?\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?Z?\]?", "", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text[:200]


def r33_chris_24h_dedup(topic: dict, ctx: dict) -> tuple[str, str] | None:
    """R33: 24h dedup for Chris's high-priority channels.

    Migrated from kaleidoscope-policy step 4.5 (AGE-239). Suppresses repeated
    notifications to #agent-ops or DM:chris that have already been delivered
    within the same UTC day. Uses normalized + hashed text so timestamp drift
    doesn't defeat the dedup.

    The dedup store is loaded into `ctx['chris_dedup_today']` by broker.check
    (a flat dict of `{hash: count}`). The store rotates daily on read.
    """
    channel = topic.get("resolved_channel") or ctx.get("resolved_channel") or ""
    if channel not in CHRIS_CHANNELS:
        return None
    text = ctx.get("message_text") or ""
    if not text:
        return None
    h = simple_hash(normalize_for_dedup(text))
    chris_store = ctx.get("chris_dedup_today") or {}
    count = chris_store.get(h)
    if count:
        return DECISION_SUPPRESS, f"Chris-channel 24h dedup (seen {count}x today)"
    return None


# Account IDs that are treated as the Weekend workspace for outbound channel filtering.
# Mirrors `confidentiality.json` `weekend_account_ids`. Hard-coded here because the
# identity is stable; the per-deployment policy (which channel is permitted) comes from
# the request context.
WEEKEND_ACCOUNT_IDS = frozenset(["weekend"])


def r35_weekend_outbound_filter(topic: dict, ctx: dict) -> tuple[str, str] | None:
    """R35: Weekend workspace outbound channel filter (AGE-2488).

    Migrated from kaleidoscope-policy step 8. When the destination account is the
    Weekend workspace, only one specific channel is allowed; all other targets are
    blocked.

    Required ctx keys for this rule to fire:
      - account_id: destination account identifier
      - channel_target: destination channel ID
      - weekend_outbound_filter.permitted_target: the only channel allowed when
        account_id is in WEEKEND_ACCOUNT_IDS

    If the policy isn't configured (no `weekend_outbound_filter` in ctx), the rule
    passes — matching current production behavior where the policy is unset.
    """
    account_id = ctx.get("account_id") or ""
    if account_id not in WEEKEND_ACCOUNT_IDS:
        return None
    filter_cfg = ctx.get("weekend_outbound_filter") or {}
    permitted = filter_cfg.get("permitted_target")
    if not permitted:
        return None
    channel = ctx.get("channel_target") or topic.get("resolved_channel") or ""
    if not channel:
        return None
    if channel == permitted:
        return None
    return (
        DECISION_SUPPRESS,
        f"Weekend workspace channel blocked — target={channel} not permitted; only {permitted} allowed (AGE-2488)",
    )


# ── R36: thin-signal gate for juno_to_chris flow ──────────────────────
# AGE-13746: enforce as code what the chris-facing-message skill enforced only
# advisorily. Suppresses Juno-to-Chris messages whose body is a status sweep
# with no decision required — the messages that R18 misses because they don't
# match the literal "all clear" markers.

_THIN_SIGNAL_PATTERNS = (
    re.compile(r"\bqueue\s+(is\s+)?healthy\b", re.IGNORECASE),
    re.compile(r"\bno\s+(further\s+)?action\s+(required|needed)\b", re.IGNORECASE),
    re.compile(r"\bno\s+anomalies\s+detected\b", re.IGNORECASE),
    re.compile(r"\ball\s+systems\s+operational\b", re.IGNORECASE),
    re.compile(r"\bsweep\s+complete\b", re.IGNORECASE),
    re.compile(r"\bnothing\s+to\s+report\b", re.IGNORECASE),
    re.compile(r"\bstatus\s+update[:\-\s]+no\s+changes\b", re.IGNORECASE),
    re.compile(r"\bstatus\s+update[:\-\s]+unchanged\b", re.IGNORECASE),
    re.compile(r"\bno\s+issues?\s+to\s+escalate\b", re.IGNORECASE),
    re.compile(r"\beverything\s+(is\s+)?(running\s+)?(normal|fine|good)\b", re.IGNORECASE),
)


def r36_thin_signal_to_chris(topic: dict, ctx: dict) -> tuple[str, str] | None:
    """R36: suppress thin-signal status sweeps in juno_to_chris flow.

    Fires only when flow == 'juno_to_chris' AND the message body matches one of
    the thin-signal patterns. These are messages that pass the broader R18
    all-clear filter but still shouldn't reach Chris — sweep reports of healthy
    queues, no-action-required status updates, and similar low-value pings.

    Migrated from the advisory chris-facing-message skill (AGE-13746) — promotes
    its three-question check from model-discretionary to code-enforced.
    """
    if ctx.get("flow") != "juno_to_chris":
        return None
    text = ctx.get("message_text") or topic.get("canonical_name") or ""
    if not text:
        return None
    for pattern in _THIN_SIGNAL_PATTERNS:
        if pattern.search(text):
            return DECISION_SUPPRESS, "Thin signal — no decision for Chris"
    return None


def r32_recent_duplicate(topic: dict, ctx: dict) -> tuple[str, str] | None:
    """R32: message text seen within the recent-dupe window → suppress.

    Migrated from kaleidoscope-policy step 4 (duplicate_message_window_ms).
    The dedup store is loaded into `ctx['recent_messages']` by broker.check
    (a flat dict of `{hash: iso_timestamp}`); this rule reads only.

    Window defaults to 300_000ms (5 minutes) and can be overridden per-call
    via `ctx['dupe_window_ms']`.
    """
    text = ctx.get("message_text") or ""
    if not text:
        return None
    window_ms = int(ctx.get("dupe_window_ms") or 300_000)
    h = simple_hash(text)
    last_seen_iso = (ctx.get("recent_messages") or {}).get(h)
    if not last_seen_iso:
        return None
    last_dt = _parse_iso(last_seen_iso)
    if last_dt is None:
        return None
    age_ms = (datetime.now(timezone.utc) - last_dt).total_seconds() * 1000
    if age_ms < window_ms:
        return DECISION_SUPPRESS, f"Duplicate message within {window_ms // 1000}s window ({age_ms / 1000:.0f}s ago)"
    return None


# Default fallthrough: surface
def _default_surface(topic: dict, ctx: dict) -> tuple[str, str]:
    return DECISION_SURFACE, "No suppression rule matched"


# Rule registry: ordered list of (rule_id, callable)
DEFAULT_RULES: list[tuple[str, Callable[[dict, dict], tuple[str, str] | None]]] = [
    # Content gates — run first; cheap heuristics with no topic-state dependency
    ("R29", r29_raw_error),
    ("R30", r30_bare_ticket_id),
    ("R31", r31_content_draft),
    ("R32", r32_recent_duplicate),
    ("R33", r33_chris_24h_dedup),
    ("R35", r35_weekend_outbound_filter),
    ("R36", r36_thin_signal_to_chris),   # AGE-13746 — thin-signal gate for juno_to_chris
    # Topic-state rules
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
