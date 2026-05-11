"""Tests for the Channel Router (AGE-13741, CFC Phase 6)."""

from __future__ import annotations

from services.router import Resolution, load_default_config, resolve


# ── basic topic-class resolution ──────────────────────────────


def test_known_topic_class_resolves_to_default_channel():
    res = resolve("legalzoom-deadline", "font_replacer", "immediate")
    assert res.channel == "C0AKKLV97PE"  # #money
    assert res.rule_id == "topic-class:legalzoom-deadline"
    assert not res.fallback_used
    assert not res.safety_violation


def test_ops_topic_routes_to_agent_ops():
    res = resolve("slack-token-expired", "font_replacer", "immediate")
    assert res.channel == "C0AKKLWGNG4"  # #agent-ops
    assert res.rule_id == "topic-class:slack-token-expired"


def test_unknown_topic_class_falls_back_to_agent_ops():
    res = resolve("nonexistent-topic-class", "age", "immediate")
    assert res.channel == "C0AKKLWGNG4"  # #agent-ops
    assert res.rule_id == "fallback:unknown-topic-class"
    assert res.fallback_used


# ── business normalization + aliases ──────────────────────────


def test_fon_alias_resolves_same_as_font_replacer():
    a = resolve("legalzoom-deadline", "fon", "immediate")
    b = resolve("legalzoom-deadline", "font_replacer", "immediate")
    assert a.channel == b.channel


def test_hyphenated_business_normalizes():
    res = resolve("legalzoom-deadline", "font-replacer", "immediate")
    expected = resolve("legalzoom-deadline", "font_replacer", "immediate")
    assert res.channel == expected.channel


def test_spaces_in_business_normalize():
    res = resolve("legalzoom-deadline", "Font Replacer", "immediate")
    expected = resolve("legalzoom-deadline", "font_replacer", "immediate")
    assert res.channel == expected.channel


# ── per-business overrides ────────────────────────────────────


def test_per_business_override_business_weekly_summary():
    """business-weekly-summary routes 'weekend' to the weekend channel."""
    res = resolve("business-weekly-summary", "weekend", "immediate")
    assert res.channel == "C0AM14ARE7N"  # #weekend
    assert "per-business" in res.rule_id


def test_per_business_falls_back_to_default():
    """Businesses not in per_business get the topic_class default."""
    res = resolve("business-weekly-summary", "age", "immediate")
    assert res.channel == "C0AKKLWGNG4"  # #agent-ops (default)


# ── tier handling ─────────────────────────────────────────────


def test_muted_tier_returns_muted():
    res = resolve("legalzoom-deadline", "font_replacer", "muted")
    assert res.channel == "MUTED"
    assert res.rule_id == "tier:muted"


def test_daily_brief_routes_to_per_business_brief():
    res = resolve("legalzoom-deadline", "font_replacer", "daily_brief")
    assert res.channel == "BRIEF:daily:font_replacer"
    assert "daily_brief" in res.rule_id


def test_weekly_brief_routes_to_per_business_brief():
    res = resolve("legalzoom-deadline", "age", "weekly_brief")
    assert res.channel == "BRIEF:weekly:age"


def test_topic_class_overrides_brief_routing():
    """queue-health-sweep has explicit daily_brief override to agent-ops."""
    res = resolve("queue-health-sweep", "age", "daily_brief")
    assert res.channel == "C0AKKLWGNG4"  # #agent-ops, not the daily brief
    assert "daily_brief" in res.rule_id


# ── override path ─────────────────────────────────────────────


def test_explicit_override_honored():
    res = resolve("legalzoom-deadline", "font_replacer", "immediate",
                  override_channel="C0AGENTOPS")
    assert res.channel == "C0AGENTOPS"
    assert res.rule_id == "override"


def test_override_symbolic_name_resolves():
    res = resolve("legalzoom-deadline", "font_replacer", "immediate",
                  override_channel="agent-ops")
    assert res.channel == "C0AKKLWGNG4"
    assert res.rule_id == "override"


# ── safety invariant: NO DM defaults ──────────────────────────


def test_safety_invariant_no_dm_in_default_config():
    """Sweep many combinations against the default config; none should return a DM."""
    cfg = load_default_config()
    for topic in list(cfg.topic_classes.keys()) + ["unknown-class-1", "unknown-class-2"]:
        for biz in ("age", "font_replacer", "fon", "weekend", "kaleidoscope", "personal", "noname"):
            for tier in ("immediate", "daily_brief", "weekly_brief"):
                res = resolve(topic, biz, tier)
                assert not res.channel.startswith("DM:"), (
                    f"resolve({topic!r}, {biz!r}, {tier!r}) returned DM target {res.channel!r}"
                )


def test_safety_invariant_dm_override_refused_without_allowlist():
    """A producer trying to override to a DM target gets refused (safety violation)."""
    res = resolve("legalzoom-deadline", "font_replacer", "immediate",
                  override_channel="DM:chris")
    assert res.safety_violation
    assert res.fallback_used
    assert not res.channel.startswith("DM:")


def test_dm_allowlist_permits_explicit_dm():
    """If a topic_class is on the dm_allowlist, override to DM works."""
    cfg = load_default_config()
    cfg.dm_allowlist.append("special-emergency-channel")
    cfg.topic_classes["special-emergency-channel"] = {"default": "DM:chris"}
    cfg.channels.setdefault("DM:chris", "DM:chris")  # noop — already a DM string
    res = resolve("special-emergency-channel", "age", "immediate", config=cfg)
    assert res.channel == "DM:chris"
    assert not res.safety_violation


# ── determinism / purity ──────────────────────────────────────


def test_resolve_is_pure_same_inputs_same_outputs():
    """Calling resolve() N times with the same inputs returns the same channel."""
    cases = [
        ("legalzoom-deadline", "font_replacer", "immediate"),
        ("slack-token-expired", "age", "immediate"),
        ("queue-health-sweep", "age", "weekly_brief"),
        ("unknown-topic", "noname", "immediate"),
    ]
    for inputs in cases:
        results = {resolve(*inputs).channel for _ in range(5)}
        assert len(results) == 1, f"Non-deterministic resolution for {inputs}: {results}"


def test_resolve_never_errors_returns_resolution():
    """Every call must return a Resolution — no exceptions for any input."""
    weird_inputs = [
        ("", "", ""),
        ("   ", "FON", "DAILY_BRIEF"),
        ("nonexistent", "<>!@#", "immediate"),
        ("ad-hoc-summary", "", "immediate"),
    ]
    for inputs in weird_inputs:
        res = resolve(*inputs)
        assert isinstance(res, Resolution)
        assert res.channel  # non-empty
