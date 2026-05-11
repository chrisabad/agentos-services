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
    # AGE-13744: per-business fallback is now #agent-ops, not DM:chris
    assert resolve_channel("age", "unknown_category", "immediate") == "C0AKKLWGNG4"


def test_resolve_channel_global_default_for_unknown_business():
    # AGE-13744: global default is now #agent-ops, not DM:chris
    assert resolve_channel("noname", "noop", "immediate") == "C0AKKLWGNG4"


def test_resolve_channel_muted_tier():
    assert resolve_channel("age", "ops", "muted") == "MUTED"


# ── AGE-13744: Font Replacer ops routing fix ────────────────────


def test_resolve_channel_font_replacer_ops_routes_to_agent_ops():
    """AGE-13744: Font Replacer ops alerts must go to #agent-ops, not DM:chris."""
    assert resolve_channel("font_replacer", "ops", "immediate") == "C0AKKLWGNG4"


def test_resolve_channel_fon_alias_collapses_to_font_replacer():
    """AGE-13744: 'fon' (FON-XXXX issue prefix) must alias to 'font_replacer'."""
    assert resolve_channel("fon", "ops", "immediate") == "C0AKKLWGNG4"


def test_resolve_channel_hyphenated_business_normalizes():
    """AGE-13744: 'font-replacer' (hyphen) must normalize to 'font_replacer'."""
    assert resolve_channel("font-replacer", "ops", "immediate") == "C0AKKLWGNG4"


def test_resolve_channel_business_aliases_work_across_categories():
    """The alias map must be applied uniformly, not just for ops."""
    assert resolve_channel("fon", "financial", "immediate") == "C0AGENTFIN1"
    assert resolve_channel("font-replacer", "financial", "immediate") == "C0AGENTFIN1"


def test_resolve_channel_safety_invariant_no_dm_default():
    """AGE-13744 safety invariant: resolve_channel must never default to DM:chris.

    The only way to get a DM resolution is via an explicit (business, category)
    mapping in _CHANNEL_BY_BUSINESS_CATEGORY (e.g., approval flows). Defaults
    and global fallback go to #agent-ops, not Chris's DMs.
    """
    # Sweep many unknown combinations — none should return a DM target.
    for business in ("noname", "fon", "font_replacer", "weekend", "kaleidoscope", "personal", "unknown_biz"):
        for category in ("unknown_category", "ops", "noop", "random"):
            result = resolve_channel(business, category, "immediate")
            if result.startswith("DM:"):
                # The only acceptable DM result is when business+category have
                # an explicit mapping to DM:chris (e.g., age + approval)
                assert (business, category) in (("age", "approval"),), (
                    f"resolve_channel({business!r}, {category!r}, 'immediate') = {result!r} "
                    f"violates no-DM-default safety invariant"
                )


def test_resolve_channel_explicit_dm_still_works():
    """Explicit DM mappings (approval flows) are still allowed — only DEFAULTS are not DM."""
    assert resolve_channel("age", "approval", "immediate") == "DM:chris"


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


# ── R36: thin-signal gate (AGE-13746) ────────────────────────────


def test_r36_suppresses_queue_healthy_to_chris():
    eng = RuleEngine()
    decision, _, rid = eng.evaluate(
        _topic(),
        {"flow": "juno_to_chris", "message_text": "Queue is healthy. No further action required."},
    )
    assert decision == DECISION_SUPPRESS
    assert rid == "R36"


def test_r36_suppresses_no_action_required():
    eng = RuleEngine()
    decision, _, rid = eng.evaluate(
        _topic(),
        {"flow": "juno_to_chris", "message_text": "Dispatch sweep complete. No further action required at this time."},
    )
    assert decision == DECISION_SUPPRESS
    assert rid == "R36"


def test_r36_suppresses_sweep_complete():
    eng = RuleEngine()
    decision, _, rid = eng.evaluate(
        _topic(),
        {"flow": "juno_to_chris", "message_text": "Dispatch sweep complete: all unassigned issues triaged."},
    )
    assert decision == DECISION_SUPPRESS
    assert rid == "R36"


def test_r36_suppresses_nothing_to_report():
    eng = RuleEngine()
    decision, _, rid = eng.evaluate(
        _topic(),
        {"flow": "juno_to_chris", "message_text": "Status update: nothing to report this hour."},
    )
    assert decision == DECISION_SUPPRESS
    assert rid == "R36"


def test_r36_does_not_fire_for_agent_to_juno_flow():
    """R36 must only fire for juno_to_chris flow — internal agent comms unaffected."""
    eng = RuleEngine()
    decision, _, rid = eng.evaluate(
        _topic(),
        {"flow": "agent_to_juno", "message_text": "Queue is healthy. No action required."},
    )
    # Falls through to default (no R36 suppression)
    assert rid != "R36"


def test_r36_does_not_suppress_real_signals():
    """R36 must let actual decision-required messages through."""
    eng = RuleEngine()
    for real_signal in (
        "FON-1313 SLACK_BOT_TOKEN expired — needs regeneration",
        "Font Replacer accounting reply from Lisa Weaver — unread 12 days",
        "LegalZoom Delaware filing deadline May 17 — 6 days away",
        "Figma support ticket closed — Font Replacer publishing blocked",
    ):
        decision, _, rid = eng.evaluate(
            _topic(),
            {"flow": "juno_to_chris", "message_text": real_signal},
        )
        assert rid != "R36", f"R36 incorrectly suppressed real signal: {real_signal!r}"


def test_r36_case_insensitive():
    """Patterns must match regardless of case."""
    eng = RuleEngine()
    for variant in (
        "QUEUE IS HEALTHY",
        "queue is healthy",
        "Queue Is Healthy",
        "No Further Action Required",
        "NO ACTION NEEDED",
    ):
        decision, _, rid = eng.evaluate(
            _topic(),
            {"flow": "juno_to_chris", "message_text": variant},
        )
        assert decision == DECISION_SUPPRESS, f"R36 missed: {variant!r}"
        assert rid == "R36"
