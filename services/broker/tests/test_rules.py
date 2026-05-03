"""Tests for the broker rule engine.

Phase 1.1 ships an 8-rule subset (R18, R20, R23, R24, R25, R19, R16, R28). The
remaining rules R01-R39 land in Phase 1.1b.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


from services.broker.rules import (
    DECISION_BATCH,
    DECISION_DECAY,
    DECISION_SUPPRESS,
    DECISION_SURFACE,
    RuleEngine,
    decay_tier,
    resolve_channel,
)


def _topic(**overrides) -> dict:
    base = {
        "fingerprint": "fp1",
        "canonical_name": "ops/build_failure/openclaw",
        "state": "triggered",
        "surface_count": 0,
        "surface_tier": "immediate",
        "last_surfaced": None,
        "producer_actions": [],
        "disposition": None,
    }
    base.update(overrides)
    return base


def now_minus_h(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


# ── decay_tier ──────────────────────────────────────────────────


def test_decay_tier_walks_chain():
    assert decay_tier("immediate") == "daily_brief"
    assert decay_tier("daily_brief") == "weekly_brief"
    assert decay_tier("weekly_brief") == "muted"
    assert decay_tier("muted") == "muted"
    assert decay_tier("unknown") == "muted"


# ── resolve_channel ─────────────────────────────────────────────


def test_resolve_channel_known_pair():
    assert resolve_channel("age", "ops", "immediate") == "C0AKKLWGNG4"


def test_resolve_channel_brief_routes():
    assert resolve_channel("age", "ops", "daily_brief") == "BRIEF:daily:age"
    assert resolve_channel("kaleidoscope", "financial", "weekly_brief") == "BRIEF:weekly:kaleidoscope"


def test_resolve_channel_falls_back_to_business_default():
    assert resolve_channel("age", "unknown_category", "immediate") == "DM:chris"


def test_resolve_channel_global_default_for_unknown_business():
    assert resolve_channel("noname", "noop", "immediate") == "DM:chris"


def test_resolve_channel_muted_tier():
    assert resolve_channel("age", "ops", "muted") == "MUTED"


# ── RuleEngine — per-rule paths ─────────────────────────────────


def test_r18_all_clear_suppresses():
    eng = RuleEngine()
    decision, reason, rid = eng.evaluate(
        _topic(canonical_name="all clear: nightly succeeded"), {}
    )
    assert decision == DECISION_SUPPRESS
    assert rid == "R18"


def test_r20_producer_already_acted_in_agent_to_juno():
    eng = RuleEngine()
    decision, reason, rid = eng.evaluate(
        _topic(producer_actions=[{"action": "comment_posted", "ts": now_minus_h(1)}]),
        {"flow": "agent_to_juno"},
    )
    assert decision == DECISION_SUPPRESS
    assert rid == "R20"


def test_r20_does_not_fire_for_juno_to_chris():
    eng = RuleEngine()
    # Same input but flow=juno_to_chris; R20 should NOT fire (R23 may, since surface_count=0 it shouldn't)
    decision, reason, rid = eng.evaluate(
        _topic(producer_actions=[{"action": "comment_posted", "ts": now_minus_h(1)}]),
        {"flow": "juno_to_chris"},
    )
    # No producer actions yet from broker's POV → falls through to default
    assert rid != "R20"


def test_r23_juno_already_surfaced():
    eng = RuleEngine()
    decision, _, rid = eng.evaluate(
        _topic(
            surface_count=1,
            last_surfaced=now_minus_h(20),  # not within 12h so R19 won't fire
            producer_actions=[{"action": "messaged_chris", "ts": now_minus_h(20)}],
        ),
        {"flow": "juno_to_chris"},
    )
    assert decision == DECISION_SUPPRESS
    assert rid == "R23"


def test_r24_acknowledged_suppresses():
    eng = RuleEngine()
    decision, _, rid = eng.evaluate(_topic(state="acknowledged"), {})
    assert decision == DECISION_SUPPRESS
    assert rid == "R24"


def test_r24_resolved_suppresses():
    eng = RuleEngine()
    decision, _, rid = eng.evaluate(_topic(state="resolved"), {})
    assert decision == DECISION_SUPPRESS
    assert rid == "R24"


def test_r25_muted_suppresses():
    eng = RuleEngine()
    decision, _, rid = eng.evaluate(_topic(state="muted"), {})
    assert decision == DECISION_SUPPRESS
    assert rid == "R25"


def test_r25_muted_with_future_expiry_suppresses():
    eng = RuleEngine()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    decision, _, rid = eng.evaluate(_topic(state="muted", muted_until=future), {})
    assert decision == DECISION_SUPPRESS
    assert rid == "R25"


def test_r25_muted_with_past_expiry_falls_through():
    eng = RuleEngine()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    # Mute expired — R25 should not suppress; default surface
    decision, _, rid = eng.evaluate(_topic(state="muted", muted_until=past), {})
    assert decision == DECISION_SURFACE
    assert rid == "DEFAULT"


def test_r19_recently_surfaced_suppresses():
    eng = RuleEngine()
    decision, _, rid = eng.evaluate(
        _topic(surface_count=1, last_surfaced=now_minus_h(2)), {}
    )
    assert decision == DECISION_SUPPRESS
    assert rid == "R19"


def test_r19_old_surface_does_not_suppress():
    eng = RuleEngine()
    decision, _, rid = eng.evaluate(
        _topic(surface_count=1, last_surfaced=now_minus_h(20)), {}
    )
    assert decision == DECISION_SURFACE
    assert rid == "DEFAULT"


def test_r16_decay_after_two_unanswered_surfaces():
    eng = RuleEngine()
    # surface_count=2, no disposition, not recently surfaced
    decision, _, rid = eng.evaluate(
        _topic(surface_count=2, state="surfaced", last_surfaced=now_minus_h(20)), {}
    )
    assert decision == DECISION_DECAY
    assert rid == "R16"


def test_r28_betterstack_warning_batches_to_brief():
    eng = RuleEngine()
    decision, _, rid = eng.evaluate(_topic(), {"source": "betterstack", "severity": "warning"})
    assert decision == DECISION_BATCH
    assert rid == "R28"


def test_default_surface_when_no_rule_matches():
    eng = RuleEngine()
    decision, _, rid = eng.evaluate(_topic(), {})
    assert decision == DECISION_SURFACE
    assert rid == "DEFAULT"


# ── Order matters: earlier rules win ────────────────────────────


def test_r24_wins_over_r19_when_both_apply():
    eng = RuleEngine()
    # acknowledged AND recently surfaced — R24 should fire (priority)
    decision, _, rid = eng.evaluate(
        _topic(state="acknowledged", surface_count=1, last_surfaced=now_minus_h(2)), {}
    )
    assert rid == "R24"


def test_failing_rule_does_not_block_engine():
    """A rule that raises should be skipped, not crash the engine."""
    def bad_rule(topic, ctx):
        raise RuntimeError("boom")

    eng = RuleEngine(rules=[("RBAD", bad_rule), ("R24", lambda t, c: (DECISION_SUPPRESS, "x") if t.get("state") == "resolved" else None)])
    decision, _, rid = eng.evaluate(_topic(state="resolved"), {})
    assert rid == "R24"
