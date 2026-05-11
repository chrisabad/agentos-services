"""Tests for the CFC reaction handlers (AGE-13742)."""

from __future__ import annotations

from services.feedback import REACTION_HANDLERS, REACTION_MAP, classify_reaction


# ── classify_reaction ───────────────────────────────────────────


def test_acted_emoji_classified():
    effect = classify_reaction("white_check_mark")
    assert effect is not None
    assert effect.code == "acted"


def test_mute_emoji_classified():
    effect = classify_reaction("mute")
    assert effect is not None
    assert effect.code == "mute_week"
    assert effect.duration_days == 7


def test_followup_emoji_classified():
    effect = classify_reaction("pushpin")
    assert effect.code == "follow_up"


def test_hard_mute_emoji_classified():
    effect = classify_reaction("no_entry_sign")
    assert effect.code == "hard_mute"


def test_unmonitored_emoji_returns_none():
    assert classify_reaction("smile") is None
    assert classify_reaction("thumbsup") is None  # NOT a CFC feedback reaction
    assert classify_reaction("") is None


# ── handler side-effect descriptions ────────────────────────────


def test_handle_acted_describes_side_effects():
    result = REACTION_HANDLERS["acted"]("notif-1", "notification", "fp-abc")
    assert result["action"] == "record_state_transition"
    assert result["new_state"] == "acted"
    assert result["suppress_duration_hours"] == 24
    assert result["suppress_fingerprint"] == "fp-abc"


def test_handle_mute_week():
    result = REACTION_HANDLERS["mute_week"]("notif-1", "notification", "legalzoom-deadline")
    assert result["action"] == "mute_topic_class"
    assert result["topic_class"] == "legalzoom-deadline"
    assert result["duration_days"] == 7


def test_handle_follow_up_default_3_days():
    result = REACTION_HANDLERS["follow_up"]("notif-1", "notification")
    assert result["action"] == "schedule_resurface"
    assert result["days"] == 3


def test_handle_hard_mute_is_indefinite():
    result = REACTION_HANDLERS["hard_mute"]("notif-1", "notification", "queue-health-sweep")
    assert result["action"] == "hard_mute_topic_class"
    assert result["duration_days"] == 0  # indefinite


def test_all_handlers_present_for_every_reaction():
    """Every entry in REACTION_MAP must have a corresponding handler."""
    for emoji, effect in REACTION_MAP.items():
        assert effect.code in REACTION_HANDLERS, (
            f"Missing handler for reaction code {effect.code} (emoji :{emoji}:)"
        )


def test_handlers_work_for_both_object_types():
    """All handlers must accept object_type='report' too, not just notification."""
    for code, handler in REACTION_HANDLERS.items():
        # Sample call shape — each handler has different signature; just verify
        # they don't crash for 'report' as object_type
        if code == "acted":
            handler("r-1", "report", "fp-x")
        elif code == "mute_week" or code == "hard_mute":
            handler("r-1", "report", "some-topic-class")
        elif code == "follow_up":
            handler("r-1", "report")
