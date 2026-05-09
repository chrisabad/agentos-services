"""Tests for the broker ledger storage layer."""

from __future__ import annotations


import pytest
from datetime import datetime, timezone

from services.broker import ledger


@pytest.fixture
def fake_ledger_path(monkeypatch, tmp_path):
    p = tmp_path / "ledger.json"
    monkeypatch.setenv("AGENTOS_BROKER_LEDGER_PATH", str(p))
    return p


def _new_topic(fp: str = "fp1", **overrides) -> dict:
    now_iso = datetime.now(timezone.utc).isoformat()
    base = {
        "fingerprint": fp,
        "canonical_name": "test/build_failure/openclaw",
        "flow": "agent_to_juno",
        "consumer": "juno",
        "business": "age",
        "category": "ops",
        "resolved_channel": "C0AKKLWGNG4",
        "related_issue_ids": [],
        "related_thread_ids": [],
        "state": "triggered",
        "first_seen": now_iso,
        "last_surfaced": None,
        "surface_count": 0,
        "surface_tier": "immediate",
        "disposition": None,
        "disposition_source": None,
        "disposition_evidence": None,
        "producer_actions": [],
        "muted_until": None,
        "last_state_change": now_iso,
    }
    base.update(overrides)
    return base


def test_load_missing_returns_empty(fake_ledger_path):
    led = ledger.load_ledger()
    # Top-level keys: version + topics + recent_messages + chris_dedup (with today's date)
    assert led["version"] == 1
    assert led["topics"] == {}
    assert led["recent_messages"] == {}
    assert led["chris_dedup"]["entries"] == {}
    assert led["chris_dedup"]["date"]  # populated with today's UTC date


def test_save_and_reload_roundtrip(fake_ledger_path):
    led = ledger.load_ledger()
    led = ledger.upsert_topic(led, _new_topic("fp1"))
    ledger.save_ledger(led)
    reloaded = ledger.load_ledger()
    assert "fp1" in reloaded["topics"]
    assert reloaded["topics"]["fp1"]["state"] == "triggered"


def test_upsert_replaces_existing(fake_ledger_path):
    led = ledger.load_ledger()
    led = ledger.upsert_topic(led, _new_topic("fp1", canonical_name="v1"))
    led = ledger.upsert_topic(led, _new_topic("fp1", canonical_name="v2"))
    assert led["topics"]["fp1"]["canonical_name"] == "v2"


def test_upsert_missing_fingerprint_raises(fake_ledger_path):
    led = ledger.load_ledger()
    with pytest.raises(ValueError):
        ledger.upsert_topic(led, {"canonical_name": "no fp"})


def test_transition_validates_state(fake_ledger_path):
    led = ledger.upsert_topic(ledger.load_ledger(), _new_topic("fp1"))
    with pytest.raises(ValueError):
        ledger.transition_topic(led, "fp1", "bogus_state")


def test_transition_unknown_topic_raises(fake_ledger_path):
    led = ledger.load_ledger()
    with pytest.raises(ValueError):
        ledger.transition_topic(led, "ghost", "resolved")


def test_transition_writes_disposition_fields(fake_ledger_path):
    led = ledger.upsert_topic(ledger.load_ledger(), _new_topic("fp1"))
    led = ledger.transition_topic(
        led, "fp1", "acknowledged",
        disposition="acknowledged",
        disposition_source="explicit",
        disposition_evidence="chris said ok in slack",
    )
    t = led["topics"]["fp1"]
    assert t["state"] == "acknowledged"
    assert t["disposition"] == "acknowledged"
    assert t["disposition_evidence"] == "chris said ok in slack"


def test_record_surface_increments_and_promotes(fake_ledger_path):
    led = ledger.upsert_topic(ledger.load_ledger(), _new_topic("fp1"))
    led = ledger.record_surface(led, "fp1", channel="DM:chris", tier="immediate")
    led = ledger.record_surface(led, "fp1", channel="DM:chris", tier="immediate")
    t = led["topics"]["fp1"]
    assert t["surface_count"] == 2
    assert t["state"] == "surfaced"
    assert t["last_surfaced"] is not None


def test_add_producer_action_appends(fake_ledger_path):
    led = ledger.upsert_topic(ledger.load_ledger(), _new_topic("fp1"))
    led = ledger.add_producer_action(led, "fp1", "comment_posted", "issue:AGE-1")
    led = ledger.add_producer_action(led, "fp1", "issue_closed")
    actions = led["topics"]["fp1"]["producer_actions"]
    assert len(actions) == 2
    assert actions[0]["action"] == "comment_posted"
    assert actions[1]["evidence_ref"] == ""


def test_prune_resolved_drops_old_resolved_only(fake_ledger_path):
    old_resolved = _new_topic("old_resolved", state="resolved", last_state_change="2024-01-01T00:00:00+00:00")
    fresh_resolved = _new_topic("fresh_resolved", state="resolved")
    triggered = _new_topic("trigd")
    led = ledger.load_ledger()
    led = ledger.upsert_topic(led, old_resolved)
    led = ledger.upsert_topic(led, fresh_resolved)
    led = ledger.upsert_topic(led, triggered)
    led = ledger.prune_resolved(led, max_age_hours=24)
    assert "old_resolved" not in led["topics"]
    assert "fresh_resolved" in led["topics"]
    assert "trigd" in led["topics"]


def test_get_stats_counts(fake_ledger_path):
    led = ledger.load_ledger()
    led = ledger.upsert_topic(led, _new_topic("a", state="triggered", surface_count=1))
    led = ledger.upsert_topic(led, _new_topic("b", state="acknowledged", surface_count=3))
    led = ledger.upsert_topic(led, _new_topic("c", state="muted", surface_tier="muted"))
    stats = ledger.get_stats(led)
    assert stats["total_topics"] == 3
    assert stats["by_state"]["acknowledged"] == 1
    assert stats["total_surfaces"] == 4
    assert stats["by_tier"]["muted"] == 1


def test_atomic_save_does_not_leave_temp(fake_ledger_path, tmp_path):
    led = ledger.load_ledger()
    led = ledger.upsert_topic(led, _new_topic("fp1"))
    ledger.save_ledger(led)
    leftover = [p for p in fake_ledger_path.parent.iterdir() if p.name.startswith(".ledger-")]
    assert leftover == []
    assert fake_ledger_path.exists()
