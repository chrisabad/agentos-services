"""Tests for the Phase 1.3 secondary endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.broker import app as broker_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPERCLIP_BOARD_KEY", "t")
    monkeypatch.setenv("AGENTOS_BROKER_LEDGER_PATH", str(tmp_path / "ledger.json"))
    from services.memory import config as memory_config

    memory_config.get_settings.cache_clear()
    return TestClient(broker_app.create_app())


HDR = {"authorization": "Bearer t"}


def _seed_topic(client) -> str:
    """Create a topic via /broker/check and return its fingerprint."""
    r = client.post(
        "/broker/check",
        headers=HDR,
        json={
            "service": "age",
            "problem_type": "build_failure",
            "resource": "openclaw",
            "canonical_name": "openclaw build failed on main",
            "flow": "agent_to_juno",
        },
    )
    assert r.status_code == 200
    return r.json()["fingerprint"]


def test_record_action_for_existing_topic(client):
    fp = _seed_topic(client)
    r = client.post(
        "/broker/record-action",
        headers=HDR,
        json={
            "service": "age",
            "problem_type": "build_failure",
            "resource": "openclaw",
            "action": "comment_posted",
            "evidence_ref": "AGE-1234",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["fingerprint"] == fp


def test_record_action_for_unknown_topic_returns_ok_false(client):
    r = client.post(
        "/broker/record-action",
        headers=HDR,
        json={
            "service": "ghost",
            "problem_type": "ghost",
            "resource": "ghost",
            "action": "noop",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is False


def test_disposition_acknowledged_then_lookup_reflects_state(client):
    fp = _seed_topic(client)
    r = client.post(
        "/broker/disposition",
        headers=HDR,
        json={
            "service": "age",
            "problem_type": "build_failure",
            "resource": "openclaw",
            "disposition": "acknowledged",
            "source": "explicit",
            "evidence": "chris said ok",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    look = client.get(f"/broker/topic/{fp}", headers=HDR)
    assert look.status_code == 200
    assert look.json()["state"] == "acknowledged"
    assert look.json()["disposition"] == "acknowledged"


def test_disposition_resolved_drops_through_R24_on_next_check(client):
    _seed_topic(client)
    client.post(
        "/broker/disposition",
        headers=HDR,
        json={
            "service": "age",
            "problem_type": "build_failure",
            "resource": "openclaw",
            "disposition": "resolved",
        },
    )
    r = client.post(
        "/broker/check",
        headers=HDR,
        json={
            "service": "age",
            "problem_type": "build_failure",
            "resource": "openclaw",
            "flow": "agent_to_juno",
        },
    )
    assert r.status_code == 200
    assert r.json()["decision"] == "suppress"
    assert r.json()["rule_id"] == "R24"


def test_topic_lookup_by_fingerprint_404_when_missing(client):
    r = client.get("/broker/topic/deadbeef" * 8, headers=HDR)
    assert r.status_code == 404


def test_topic_lookup_post_with_natural_key(client):
    _seed_topic(client)
    r = client.post(
        "/broker/topic/lookup",
        headers=HDR,
        json={
            "service": "age",
            "problem_type": "build_failure",
            "resource": "openclaw",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["canonical_name"] == "openclaw build failed on main"


def test_standing_decisions_lists_rules(client):
    r = client.get("/broker/standing-decisions", headers=HDR)
    assert r.status_code == 200
    rules = r.json()["rules"]
    assert any(rule["rule_id"] == "R18" for rule in rules)
    assert all("name" in rule and "doc" in rule for rule in rules)


def test_stats_after_seeding(client):
    _seed_topic(client)
    r = client.get("/broker/stats", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["total_topics"] >= 1
    assert "by_state" in body


def test_unauth_requires_bearer(client):
    r = client.get("/broker/stats")
    assert r.status_code == 401
