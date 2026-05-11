"""Channel Router service — pure-function (topic_class, business, tier) → channel.

Replaces the broker's `resolve_channel()` with a YAML-driven, no-DM-defaults
router. Part of the Chris-facing Communications redesign (AGE-13735, Phase 6).

Public API:
    from services.router import resolve, Resolution, load_config

    res = resolve("legalzoom-deadline", "font_replacer", "immediate")
    # → Resolution(channel="C0AKKLV97PE", rule_id="topic-class:legalzoom-deadline", ...)
"""

from services.router.resolve import (
    Resolution,
    RouterConfig,
    load_config,
    load_default_config,
    resolve,
)

__all__ = ["Resolution", "RouterConfig", "load_config", "load_default_config", "resolve"]
