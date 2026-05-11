"""Reaction-to-side-effect mapping for CFC feedback (AGE-13742).

Slack `reaction_added` events for monitored emojis produce structured side
effects on the underlying Notification or Report object. This module defines
the mapping and per-reaction handler stubs; the wiring to the Notification
Service / Report Service state mutations happens in Phase 3 and Phase 4.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

ObjectType = Literal["notification", "report"]


@dataclass(frozen=True)
class ReactionEffect:
    """Description of what a reaction does."""

    code: str              # internal reaction code (e.g., 'acted', 'mute_week')
    description: str       # human-readable explanation
    duration_days: int = 0  # 0 = not time-bounded (act / hard-mute)


# Emoji → ReactionEffect. Slack delivers reaction names without leading/trailing colons.
REACTION_MAP: dict[str, ReactionEffect] = {
    "white_check_mark": ReactionEffect(
        code="acted",
        description="Handled — suppress same fingerprint for 24h",
        duration_days=1,
    ),
    "mute": ReactionEffect(
        code="mute_week",
        description="Mute this topic class for a week",
        duration_days=7,
    ),
    "pushpin": ReactionEffect(
        code="follow_up",
        description="Follow up later — schedule re-surface",
        duration_days=3,
    ),
    "no_entry_sign": ReactionEffect(
        code="hard_mute",
        description="Never surface this topic again — manual unmute required",
        duration_days=0,
    ),
}


def classify_reaction(emoji_name: str) -> ReactionEffect | None:
    """Translate a Slack reaction name to its CFC effect.

    Returns None for unmonitored emojis (most reactions — we only care about
    the four explicit feedback emojis).
    """
    return REACTION_MAP.get(emoji_name)


# ── Handler stubs ────────────────────────────────────────────
# These are stubs only — the wiring to Notification Service / Report Service
# state mutations happens in Phase 3 and Phase 4 endpoint integration.


def handle_acted(object_id: str, object_type: ObjectType, fingerprint: str) -> dict:
    """✅ — record acted, suppress fingerprint for 24h.

    Returns a description of intended side effects (used in tests + to
    document what the eventual integration must do).
    """
    return {
        "action": "record_state_transition",
        "object_id": object_id,
        "object_type": object_type,
        "new_state": "acted",
        "suppress_fingerprint": fingerprint,
        "suppress_duration_hours": 24,
    }


def handle_mute_week(object_id: str, object_type: ObjectType, topic_class: str) -> dict:
    """🔇 — mute topic_class for 7d."""
    return {
        "action": "mute_topic_class",
        "object_id": object_id,
        "object_type": object_type,
        "topic_class": topic_class,
        "duration_days": 7,
    }


def handle_follow_up(object_id: str, object_type: ObjectType, days: int = 3) -> dict:
    """📌 — schedule re-surface in N days."""
    return {
        "action": "schedule_resurface",
        "object_id": object_id,
        "object_type": object_type,
        "days": days,
    }


def handle_hard_mute(object_id: str, object_type: ObjectType, topic_class: str) -> dict:
    """🚫 — hard-mute topic_class indefinitely."""
    return {
        "action": "hard_mute_topic_class",
        "object_id": object_id,
        "object_type": object_type,
        "topic_class": topic_class,
        "duration_days": 0,   # 0 == indefinite
    }


REACTION_HANDLERS: dict[str, Callable] = {
    "acted": handle_acted,
    "mute_week": handle_mute_week,
    "follow_up": handle_follow_up,
    "hard_mute": handle_hard_mute,
}
