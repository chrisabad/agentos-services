"""Channel Router — pure-function (topic_class, business, tier) → channel.

AGE-13741 / CFC Phase 6. Replaces the broker's `resolve_channel()` with three
deliberate property changes:

1. Pure function — same inputs always yield same output. No hidden state.
2. No DM defaults — DM routing requires an explicit allowlist entry. Default
   fall-through goes to #agent-ops, never to a Chris DM.
3. Topic-class-first — routing decisions are made primarily by topic_class;
   business and tier are secondary modifiers.

Safety invariant: `resolve()` will never return a channel starting with "DM:"
unless the corresponding topic_class is explicitly in `dm_allowlist`. Misconfiguration
(e.g., a non-allowlisted topic_class mapped to a DM symbol) is treated as a bug
and falls back to default_channel with `safety_violation=True` in the result.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

LOG = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).parent / "default-channels.yaml"


@dataclass(frozen=True)
class Resolution:
    """Result of a single channel resolution call."""

    channel: str
    rule_id: str
    fallback_used: bool = False
    safety_violation: bool = False
    notes: str = ""


@dataclass
class RouterConfig:
    """Loaded router configuration. Construct via `load_config()` or `load_default_config()`."""

    channels: dict[str, str] = field(default_factory=dict)
    brief_channels: dict[str, dict[str, str]] = field(default_factory=dict)
    topic_classes: dict[str, dict[str, Any]] = field(default_factory=dict)
    default_channel: str = "agent-ops"
    business_aliases: dict[str, str] = field(default_factory=dict)
    dm_allowlist: list[str] = field(default_factory=list)

    def get_channel_id(self, symbolic_name: str) -> str:
        """Resolve a symbolic channel name (e.g., 'agent-ops') to its ID.

        Returns the input unchanged if it already looks like a Slack ID,
        a BRIEF target, or a DM target.
        """
        if not symbolic_name:
            return ""
        if symbolic_name.startswith(("BRIEF:", "DM:", "C0", "C1", "G0", "G1", "MUTED")):
            return symbolic_name
        return self.channels.get(symbolic_name, symbolic_name)

    def normalize_business(self, business: str) -> str:
        """Apply business name normalization + alias lookup.

        Mirrors the broker's `_normalize_business` in `rules.py`. Hyphens and
        spaces collapse to underscores; aliases (e.g., 'fon' → 'font_replacer')
        are applied so producers can use any reasonable form.
        """
        if not business:
            return ""
        norm = business.lower().strip().replace("-", "_").replace(" ", "_")
        return self.business_aliases.get(norm, norm)


def load_default_config() -> RouterConfig:
    """Load the embedded default-channels.yaml shipped with the package."""
    return load_config(_DEFAULT_CONFIG_PATH)


def load_config(path: Path | str) -> RouterConfig:
    """Load router config from a YAML file path.

    Production config typically lives at `agentos-config/router/channels.yaml`
    and overrides the embedded default.
    """
    with open(path, "r") as f:
        raw = yaml.safe_load(f) or {}
    return RouterConfig(
        channels=raw.get("channels", {}) or {},
        brief_channels=raw.get("brief_channels", {}) or {},
        topic_classes=raw.get("topic_classes", {}) or {},
        default_channel=raw.get("default_channel", "agent-ops"),
        business_aliases=raw.get("business_aliases", {}) or {},
        dm_allowlist=list(raw.get("dm_allowlist", []) or []),
    )


def resolve(
    topic_class: str,
    business: str = "",
    tier: str = "immediate",
    *,
    override_channel: str | None = None,
    config: RouterConfig | None = None,
) -> Resolution:
    """Resolve a (topic_class, business, tier) to a delivery channel.

    Args:
        topic_class: The registered topic class for this signal (must match
            a key in config.topic_classes, or fall back to default_channel).
        business: Business context. Hyphens, spaces, and known aliases are
            normalized (e.g., 'fon' → 'font_replacer').
        tier: 'immediate' | 'daily_brief' | 'weekly_brief' | 'muted'.
            Brief tiers route to per-business brief channels; muted returns 'MUTED'.
        override_channel: If provided, used directly (with safety check applied).
            For producers that already know their destination.
        config: Optional RouterConfig override; defaults to load_default_config().

    Returns:
        Resolution with the channel ID, the rule that fired, and safety flags.
    """
    cfg = config or load_default_config()

    # Explicit override — honor but enforce safety invariant
    if override_channel:
        resolved = cfg.get_channel_id(override_channel)
        if _is_dm(resolved) and topic_class not in cfg.dm_allowlist:
            LOG.warning(
                "DM override refused: topic_class=%s, override=%s — falling back to default",
                topic_class, override_channel,
            )
            return Resolution(
                channel=cfg.get_channel_id(cfg.default_channel),
                rule_id="safety:dm-override-not-allowlisted",
                fallback_used=True,
                safety_violation=True,
                notes=f"override {override_channel!r} refused (DM not in allowlist)",
            )
        return Resolution(channel=resolved, rule_id="override", notes="explicit override")

    business_norm = cfg.normalize_business(business)
    tier_norm = (tier or "immediate").lower()

    # Muted tier — always return MUTED, regardless of topic_class
    if tier_norm == "muted":
        return Resolution(channel="MUTED", rule_id="tier:muted")

    # Topic-class lookup
    entry = cfg.topic_classes.get(topic_class)

    if entry is None:
        # Unknown topic_class — fall back to default_channel
        return Resolution(
            channel=cfg.get_channel_id(cfg.default_channel),
            rule_id="fallback:unknown-topic-class",
            fallback_used=True,
            notes=f"topic_class {topic_class!r} not in registry",
        )

    # Brief tiers
    if tier_norm in ("daily_brief", "weekly_brief"):
        # Topic-class level override for this tier?
        if tier_norm in entry:
            tier_override = entry[tier_norm]
            if tier_override is None:
                # Explicit null in YAML means "never surface at this tier"
                return Resolution(
                    channel="MUTED",
                    rule_id=f"{tier_norm}:explicit-null",
                    notes=f"topic_class {topic_class!r} explicitly suppresses at {tier_norm}",
                )
            return Resolution(
                channel=cfg.get_channel_id(tier_override),
                rule_id=f"{tier_norm}:{topic_class}",
            )
        # Default to per-business brief channel
        brief_kind = tier_norm.replace("_brief", "")
        brief_map = cfg.brief_channels.get(brief_kind, {})
        chan = brief_map.get(business_norm) or brief_map.get("age") or "MUTED"
        return Resolution(channel=chan, rule_id=f"{tier_norm}:brief:{business_norm}")

    # Immediate tier — per-business override or default
    per_business = entry.get("per_business") or {}
    if business_norm in per_business:
        return Resolution(
            channel=cfg.get_channel_id(per_business[business_norm]),
            rule_id=f"per-business:{topic_class}:{business_norm}",
        )

    chan_symbolic = entry.get("default")
    if chan_symbolic is None:
        # Topic class exists but has no default — fall back to global default
        return Resolution(
            channel=cfg.get_channel_id(cfg.default_channel),
            rule_id=f"fallback:no-default:{topic_class}",
            fallback_used=True,
            notes=f"topic_class {topic_class!r} has no default channel",
        )

    resolved = cfg.get_channel_id(chan_symbolic)

    # Safety invariant: refuse DM unless explicitly allowlisted
    if _is_dm(resolved) and topic_class not in cfg.dm_allowlist:
        LOG.warning(
            "DM routing refused: topic_class=%s resolved=%s not in dm_allowlist",
            topic_class, resolved,
        )
        return Resolution(
            channel=cfg.get_channel_id(cfg.default_channel),
            rule_id="safety:dm-not-allowlisted",
            fallback_used=True,
            safety_violation=True,
            notes=(
                f"topic_class {topic_class!r} resolved to {resolved!r} (a DM) but "
                f"is not in dm_allowlist; refusing and routing to default_channel"
            ),
        )

    return Resolution(channel=resolved, rule_id=f"topic-class:{topic_class}")


def _is_dm(channel: str) -> bool:
    """True if the channel string looks like a DM target."""
    return bool(channel) and channel.startswith("DM:")
