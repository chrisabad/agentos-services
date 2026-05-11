"""Tests for the personal business/category lane routing (AGE-13645).

All `personal` business categories route to #general (C0GENERAL), including
unknown/empty categories via the default fallback.
"""

from __future__ import annotations

from services.broker.rules import resolve_channel


# ── Explicit category mappings ──────────────────────────────────────


def test_personal_benefits_routes_to_general():
    assert resolve_channel("personal", "benefits", "immediate") == "C0GENERAL"


def test_personal_health_routes_to_general():
    assert resolve_channel("personal", "health", "immediate") == "C0GENERAL"


def test_personal_finance_routes_to_general():
    assert resolve_channel("personal", "finance", "immediate") == "C0GENERAL"


def test_personal_household_routes_to_general():
    assert resolve_channel("personal", "household", "immediate") == "C0GENERAL"


# ── Catch-all / default fallback ────────────────────────────────────


def test_personal_unknown_category_routes_to_general():
    """Unknown personal category falls through to the business default → C0GENERAL."""
    assert resolve_channel("personal", "unknown_category", "immediate") == "C0GENERAL"


def test_personal_empty_category_routes_to_general():
    """Empty category falls through to business default → C0GENERAL."""
    assert resolve_channel("personal", "", "immediate") == "C0GENERAL"


def test_personal_no_category_routes_to_general():
    """None/missing category uses business default → C0GENERAL."""
    assert resolve_channel("personal", None, "immediate") == "C0GENERAL"


# ── Brief and muted tier routing ────────────────────────────────────


def test_personal_daily_brief_routing():
    """Briefs use the brief channel format, not C0GENERAL."""
    assert resolve_channel("personal", "benefits", "daily_brief") == "BRIEF:daily:personal"


def test_personal_weekly_brief_routing():
    assert resolve_channel("personal", "health", "weekly_brief") == "BRIEF:weekly:personal"


def test_personal_muted_tier():
    """Muted tier always returns MUTED regardless of business/category."""
    assert resolve_channel("personal", "benefits", "muted") == "MUTED"


# ── Regression: existing routing unchanged ──────────────────────────


def test_age_ops_still_routes_to_agent_ops():
    """Pre-existing routing must not be affected by the personal lane."""
    assert resolve_channel("age", "ops", "immediate") == "C0AKKLWGNG4"


def test_kaleidoscope_default_routes_to_agent_ops():
    """AGE-13744: per-business defaults are now #agent-ops, not DM:chris.

    Routing to a Chris DM requires an explicit (business, category) mapping;
    defaults and global fallback land in #agent-ops so they can be audited
    and the registry corrected.
    """
    assert resolve_channel("kaleidoscope", "unknown_category", "immediate") == "C0AGENTOPS"