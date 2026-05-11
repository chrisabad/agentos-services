"""Feedback service — Slack Block Kit feedback affordances + reaction handlers.

CFC Phase 7 (AGE-13742). Provides:

1. Block Kit templates for the `feedback_buttons` element attached to every
   Juno-to-Chris message (notifications + reports)
2. Modal definitions for the 👎 path with separate reason vocabularies for
   notifications vs reports
3. Reaction handlers that translate Slack `reaction_added` events into
   structured feedback + side-effects (mute, snooze, hard-mute)

This module is library-only — the HTTP layer that receives Slack `block_actions`
and `reaction_added` events is part of the Notification Service's Slack
integration (Phase 3.2 / Phase 5).
"""

from services.feedback.blocks import (
    feedback_buttons_block,
    feedback_context_block,
    negative_feedback_modal_notification,
    negative_feedback_modal_report,
)
from services.feedback.reactions import (
    REACTION_HANDLERS,
    REACTION_MAP,
    classify_reaction,
)

__all__ = [
    "feedback_buttons_block",
    "feedback_context_block",
    "negative_feedback_modal_notification",
    "negative_feedback_modal_report",
    "REACTION_HANDLERS",
    "REACTION_MAP",
    "classify_reaction",
]
