"""Tests for the policy-migration rules R29-R32 (Phase 1.1b).

These rules were migrated from `kaleidoscope-policy/index.js` steps 1-4
(raw errors, bare ticket IDs, content drafts, 5-minute duplicates).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from services.broker.rules import (
    DECISION_SUPPRESS,
    RuleEngine,
    r29_raw_error,
    r30_bare_ticket_id,
    r31_content_draft,
    r32_recent_duplicate,
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
    }
    base.update(overrides)
    return base


# ── R29 raw error ────────────────────────────────────────────────


def test_r29_detects_stack_trace_line():
    ctx = {"message_text": "    at Object.<anonymous> (script.js:12:5)"}
    verdict = r29_raw_error(_topic(), ctx)
    assert verdict is not None
    assert verdict[0] == DECISION_SUPPRESS
    assert "raw error" in verdict[1].lower() or "stack trace" in verdict[1].lower()


def test_r29_detects_http_error():
    assert r29_raw_error(_topic(), {"message_text": "HTTP Error 404: Not Found"}) is not None


def test_r29_detects_traceback():
    text = "Traceback (most recent call last):\n  File ..."
    assert r29_raw_error(_topic(), {"message_text": text}) is not None


def test_r29_detects_enoent():
    assert r29_raw_error(_topic(), {"message_text": "ENOENT: no such file"}) is not None


def test_r29_passes_normal_message():
    assert r29_raw_error(_topic(), {"message_text": "Everything looks good — KAL-24 shipped"}) is None


def test_r29_passes_empty_text():
    assert r29_raw_error(_topic(), {}) is None
    assert r29_raw_error(_topic(), {"message_text": ""}) is None


# ── R30 bare ticket ID ──────────────────────────────────────────


def test_r30_detects_single_bare_id():
    assert r30_bare_ticket_id(_topic(), {"message_text": "AGE-24"}) is not None


def test_r30_detects_multiple_bare_ids():
    assert r30_bare_ticket_id(_topic(), {"message_text": "AGE-24, AGE-25"}) is not None


def test_r30_passes_id_with_context():
    ctx = {"message_text": "AGE-24 is now blocked on legal review"}
    assert r30_bare_ticket_id(_topic(), ctx) is None


def test_r30_passes_empty_text():
    assert r30_bare_ticket_id(_topic(), {}) is None


# ── R31 content draft ──────────────────────────────────────────


def test_r31_detects_obvious_draft():
    text = (
        "Most teams are doing it completely wrong, and the cost is enormous.\n\n"
        "Here is why it matters and why I think you should care about it.\n\n"
        "Most leaders simply miss it because they are not looking carefully.\n\n"
        "But the underlying data is genuinely clear if you know where to look.\n\n"
        "Subscribe to studiomethod.ai for the full breakdown and more details.\n\n"
        "#leadership #strategy"
    )
    assert len(text) >= 200
    assert r31_content_draft(_topic(), {"message_text": text}) is not None


def test_r31_passes_short_message():
    assert r31_content_draft(_topic(), {"message_text": "Quick update on KAL-24"}) is None


def test_r31_passes_long_no_cta():
    text = "Long technical update " * 30 + "\n\n\n\n\n no marketing here"
    assert r31_content_draft(_topic(), {"message_text": text}) is None


# ── R32 recent duplicate ───────────────────────────────────────


def test_r32_passes_when_no_text():
    assert r32_recent_duplicate(_topic(), {}) is None


def test_r32_passes_when_no_recent_messages():
    ctx = {"message_text": "Build complete"}
    assert r32_recent_duplicate(_topic(), ctx) is None


def test_r32_suppresses_within_window():
    text = "Build complete"
    h = simple_hash(text)
    iso = datetime.now(timezone.utc).isoformat()
    ctx = {"message_text": text, "recent_messages": {h: iso}}
    verdict = r32_recent_duplicate(_topic(), ctx)
    assert verdict is not None
    assert verdict[0] == DECISION_SUPPRESS


def test_r32_passes_outside_window():
    text = "Build complete"
    h = simple_hash(text)
    iso = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    ctx = {"message_text": text, "recent_messages": {h: iso}}
    assert r32_recent_duplicate(_topic(), ctx) is None


def test_r32_window_override():
    text = "Build complete"
    h = simple_hash(text)
    iso = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    ctx = {
        "message_text": text,
        "recent_messages": {h: iso},
        "dupe_window_ms": 10_000,  # 10s — entry is older
    }
    assert r32_recent_duplicate(_topic(), ctx) is None


# ── Engine integration: priority ordering ────────────────────────


def test_engine_runs_r29_before_topic_state_rules():
    """Even on a topic with no message text condition match, R29 should fire
    when message_text contains an error, since the content gates run first."""
    eng = RuleEngine()
    topic = _topic(state="acknowledged")  # would normally trigger R24
    ctx = {"message_text": "Traceback (most recent call last):"}
    decision, reason, rule_id = eng.evaluate(topic, ctx)
    assert decision == DECISION_SUPPRESS
    assert rule_id == "R29"


def test_engine_falls_through_to_r24_when_no_content_issue():
    eng = RuleEngine()
    topic = _topic(state="acknowledged")
    ctx = {"message_text": "Reminder: standup at 10"}
    decision, reason, rule_id = eng.evaluate(topic, ctx)
    assert decision == DECISION_SUPPRESS
    assert rule_id == "R24"


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


def test_e2e_r29_raw_error_suppresses(client):
    r = client.post(
        "/broker/check",
        headers=HDR,
        json={
            "service": "age",
            "problem_type": "build_failure",
            "resource": "openclaw",
            "canonical_name": "openclaw build failed",
            "flow": "agent_to_juno",
            "context": {"message_text": "Traceback (most recent call last):"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decision"] == "suppress"
    assert body["rule_id"] == "R29"


def test_e2e_r32_dedup_via_repeated_call(client):
    payload = {
        "service": "age",
        "problem_type": "shipping",
        "resource": "kal_release",
        "canonical_name": "kaleidoscope release shipped",
        "flow": "juno_to_chris",
        "context": {"message_text": "KAL release shipped, all 4 services green"},
    }
    r1 = client.post("/broker/check", headers=HDR, json=payload)
    assert r1.status_code == 200, r1.text
    assert r1.json()["decision"] == "surface"

    # Second identical call should hit R32 and suppress.
    r2 = client.post("/broker/check", headers=HDR, json=payload)
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["decision"] == "suppress"
    assert body["rule_id"] == "R32"
