"""Tests for R33: 24h dedup on Chris's high-priority Slack channels.

Migrated from kaleidoscope-policy step 4.5 (AGE-239).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.broker import ledger
from services.broker.rules import (
    CHRIS_CHANNELS,
    DECISION_SUPPRESS,
    normalize_for_dedup,
    r33_chris_24h_dedup,
    simple_hash,
)


def _topic(**overrides) -> dict:
    base = {
        "fingerprint": "fp1",
        "canonical_name": "ops/build/openclaw",
        "state": "triggered",
        "surface_count": 0,
        "surface_tier": "immediate",
        "last_surfaced": None,
        "producer_actions": [],
        "disposition": None,
        "resolved_channel": "C0AKKLWGNG4",  # #agent-ops
    }
    base.update(overrides)
    return base


CHRIS_CH = "C0AKKLWGNG4"  # #agent-ops
NON_CHRIS_CH = "C0SOMEOTHERCHAN"


# ── normalize_for_dedup ─────────────────────────────────────────


def test_normalize_strips_iso_timestamps():
    raw = "[2026-05-03T14:09:12Z] gateway restarted"
    norm = normalize_for_dedup(raw)
    assert "2026" not in norm
    assert "gateway restarted" in norm


def test_normalize_collapses_whitespace_and_lowercases():
    norm = normalize_for_dedup("  Build   FAILED   \n  on main  ")
    assert norm == "build failed on main"


def test_normalize_truncates_at_200_chars():
    raw = "x" * 500
    assert len(normalize_for_dedup(raw)) == 200


# ── R33 rule ────────────────────────────────────────────────────


def test_r33_passes_for_non_chris_channel():
    topic = _topic(resolved_channel=NON_CHRIS_CH)
    h = simple_hash(normalize_for_dedup("repeated message"))
    ctx = {"message_text": "repeated message", "chris_dedup_today": {h: 5}}
    assert r33_chris_24h_dedup(topic, ctx) is None


def test_r33_passes_when_no_dedup_entry():
    topic = _topic(resolved_channel=CHRIS_CH)
    ctx = {"message_text": "first time we send this", "chris_dedup_today": {}}
    assert r33_chris_24h_dedup(topic, ctx) is None


def test_r33_suppresses_when_seen_today():
    topic = _topic(resolved_channel=CHRIS_CH)
    text = "kaleidoscope deployment incident"
    h = simple_hash(normalize_for_dedup(text))
    ctx = {"message_text": text, "chris_dedup_today": {h: 3}}
    verdict = r33_chris_24h_dedup(topic, ctx)
    assert verdict is not None
    assert verdict[0] == DECISION_SUPPRESS
    assert "3x today" in verdict[1]


def test_r33_normalizes_so_timestamps_dont_defeat_dedup():
    topic = _topic(resolved_channel=CHRIS_CH)
    # First call records the normalized hash (no timestamp prefix)
    text_a = "kaleidoscope deployment incident"
    h = simple_hash(normalize_for_dedup(text_a))
    # Second call has a timestamp prefix but same body — should match
    text_b = "[2026-05-03T14:09:12Z] kaleidoscope deployment incident"
    ctx = {"message_text": text_b, "chris_dedup_today": {h: 1}}
    verdict = r33_chris_24h_dedup(topic, ctx)
    assert verdict is not None
    assert verdict[0] == DECISION_SUPPRESS


def test_r33_passes_when_no_text():
    topic = _topic(resolved_channel=CHRIS_CH)
    assert r33_chris_24h_dedup(topic, {}) is None


# ── ledger plumbing ─────────────────────────────────────────────


def test_record_chris_dedup_increments_count(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOS_BROKER_LEDGER_PATH", str(tmp_path / "ledger.json"))
    led = ledger.load_ledger()
    led = ledger.record_chris_dedup(led, "abc123")
    led = ledger.record_chris_dedup(led, "abc123")
    led = ledger.record_chris_dedup(led, "abc123")
    assert led["chris_dedup"]["entries"]["abc123"] == 3


def test_record_chris_dedup_rotates_on_date_change(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOS_BROKER_LEDGER_PATH", str(tmp_path / "ledger.json"))
    led = ledger.load_ledger()
    led["chris_dedup"] = {"date": "2025-01-01", "entries": {"old_hash": 99}}
    led = ledger.record_chris_dedup(led, "new_hash")
    # Old date's entries should be gone; new date should be today's
    assert led["chris_dedup"]["entries"] == {"new_hash": 1}
    assert led["chris_dedup"]["date"] != "2025-01-01"


def test_load_ledger_rotates_stale_chris_dedup(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOS_BROKER_LEDGER_PATH", str(tmp_path / "ledger.json"))
    # Save a ledger with yesterday's date pre-populated
    stale = {
        "version": 1,
        "topics": {},
        "recent_messages": {},
        "chris_dedup": {"date": "2025-01-01", "entries": {"old": 7}},
    }
    ledger.save_ledger(stale)
    # Reload — date roll should reset entries
    fresh = ledger.load_ledger()
    assert fresh["chris_dedup"]["entries"] == {}
    assert fresh["chris_dedup"]["date"] != "2025-01-01"


# ── End-to-end via /broker/check ─────────────────────────────────


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPERCLIP_BOARD_KEY", "test-token")
    monkeypatch.setenv("AGENTOS_BROKER_LEDGER_PATH", str(tmp_path / "ledger.json"))
    from services.memory import config as memory_config

    memory_config.get_settings.cache_clear()

    from services.broker import app as broker_app

    return TestClient(broker_app.create_app())


HDR = {"authorization": "Bearer test-token"}


def test_e2e_r33_dedup_via_repeated_call_to_chris_channel(client):
    payload = {
        "service": "age",
        "problem_type": "ops",  # routes to #agent-ops via resolve_channel
        "resource": "gateway_health",
        "canonical_name": "gateway health alert",
        "flow": "juno_to_chris",
        "category": "ops",
        "context": {"message_text": "gateway restart in progress"},
        # Stretch the recent-dupe window so R32 doesn't shadow R33 on the second
        # call — we want to verify R33 specifically.
    }
    r1 = client.post("/broker/check", headers=HDR, json=payload)
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["decision"] == "surface"
    assert body1["resolved_channel"] == CHRIS_CH

    # Second call with R32 disabled (very short dupe window) — only R33 should trigger
    payload_followup = {**payload}
    payload_followup["context"] = {
        "message_text": "gateway restart in progress",
        "dupe_window_ms": 1,  # effectively disable R32
    }
    r2 = client.post("/broker/check", headers=HDR, json=payload_followup)
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["decision"] == "suppress"
    assert body2["rule_id"] == "R33"


def test_e2e_r33_does_not_apply_to_non_chris_channel(client):
    """Non-#agent-ops channels (e.g., financial) should not be subject to R33."""
    # Use category=financial to route somewhere that isn't a CHRIS_CHANNEL
    payload = {
        "service": "kaleidoscope",
        "problem_type": "billing",
        "resource": "stripe_balance",
        "canonical_name": "stripe balance below threshold",
        "flow": "juno_to_chris",
        "category": "financial",
        "context": {"message_text": "stripe balance is now $1234.56"},
    }
    r1 = client.post("/broker/check", headers=HDR, json=payload)
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["decision"] == "surface"
    # resolved_channel should NOT be a Chris channel
    assert body1["resolved_channel"] not in CHRIS_CHANNELS
