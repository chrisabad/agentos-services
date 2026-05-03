"""Tests for R35: Weekend workspace outbound channel filter (AGE-2488).

Migrated from kaleidoscope-policy step 8.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.broker.rules import (
    DECISION_SUPPRESS,
    WEEKEND_ACCOUNT_IDS,
    r35_weekend_outbound_filter,
)


def _topic(**overrides) -> dict:
    base = {
        "fingerprint": "fp1",
        "canonical_name": "ops/x/y",
        "state": "triggered",
        "surface_count": 0,
        "surface_tier": "immediate",
        "last_surfaced": None,
        "producer_actions": [],
        "disposition": None,
        "resolved_channel": None,
    }
    base.update(overrides)
    return base


PERMITTED_CH = "DM_PERMITTED_TARGET"
BLOCKED_CH = "C_RANDOM_CHANNEL"


# ── Rule logic ───────────────────────────────────────────────────


def test_r35_passes_when_account_not_weekend():
    ctx = {
        "account_id": "kaleidoscope",
        "channel_target": BLOCKED_CH,
        "weekend_outbound_filter": {"permitted_target": PERMITTED_CH},
    }
    assert r35_weekend_outbound_filter(_topic(), ctx) is None


def test_r35_passes_when_filter_not_configured():
    """If `weekend_outbound_filter` isn't in ctx, the rule is inert."""
    ctx = {"account_id": "weekend", "channel_target": BLOCKED_CH}
    assert r35_weekend_outbound_filter(_topic(), ctx) is None


def test_r35_passes_when_destination_matches_permitted():
    ctx = {
        "account_id": "weekend",
        "channel_target": PERMITTED_CH,
        "weekend_outbound_filter": {"permitted_target": PERMITTED_CH},
    }
    assert r35_weekend_outbound_filter(_topic(), ctx) is None


def test_r35_suppresses_blocked_channel():
    ctx = {
        "account_id": "weekend",
        "channel_target": BLOCKED_CH,
        "weekend_outbound_filter": {"permitted_target": PERMITTED_CH},
    }
    verdict = r35_weekend_outbound_filter(_topic(), ctx)
    assert verdict is not None
    assert verdict[0] == DECISION_SUPPRESS
    assert "AGE-2488" in verdict[1]
    assert PERMITTED_CH in verdict[1]


def test_r35_falls_back_to_topic_resolved_channel():
    """If `channel_target` isn't in ctx, fall back to topic's resolved_channel."""
    topic = _topic(resolved_channel=BLOCKED_CH)
    ctx = {
        "account_id": "weekend",
        "weekend_outbound_filter": {"permitted_target": PERMITTED_CH},
    }
    verdict = r35_weekend_outbound_filter(topic, ctx)
    assert verdict is not None
    assert verdict[0] == DECISION_SUPPRESS


def test_r35_passes_when_no_channel_anywhere():
    """No channel in ctx, no channel on topic — nothing to enforce against."""
    ctx = {
        "account_id": "weekend",
        "weekend_outbound_filter": {"permitted_target": PERMITTED_CH},
    }
    assert r35_weekend_outbound_filter(_topic(), ctx) is None


def test_weekend_account_ids_constant_matches_confidentiality_json():
    """Sanity: the constant should mirror `weekend_account_ids` in
    `~/.openclaw/workspace/policy/confidentiality.json` (currently `["weekend"]`)."""
    assert "weekend" in WEEKEND_ACCOUNT_IDS


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


def test_e2e_r35_blocks_weekend_outbound_to_disallowed_channel(client):
    payload = {
        "service": "weekend",
        "problem_type": "ops",
        "resource": "team_update",
        "canonical_name": "weekend team status update",
        "flow": "agent_to_juno",
        "context": {
            "message_text": "shipping status looks great",
            "account_id": "weekend",
            "channel_target": BLOCKED_CH,
            "weekend_outbound_filter": {"permitted_target": PERMITTED_CH},
        },
    }
    r = client.post("/broker/check", headers=HDR, json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decision"] == "suppress"
    assert body["rule_id"] == "R35"


def test_e2e_r35_inert_when_policy_not_configured(client):
    """No `weekend_outbound_filter` → R35 doesn't fire; message surfaces."""
    payload = {
        "service": "weekend",
        "problem_type": "ops",
        "resource": "team_update_2",
        "canonical_name": "weekend team status update 2",
        "flow": "agent_to_juno",
        "context": {
            "message_text": "another shipping status",
            "account_id": "weekend",
            "channel_target": BLOCKED_CH,
        },
    }
    r = client.post("/broker/check", headers=HDR, json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    # Should surface — R35 is inert without the filter config
    assert body["decision"] == "surface"
    assert body["rule_id"] == "DEFAULT"
