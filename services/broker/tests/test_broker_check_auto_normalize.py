"""Tests for /broker/check auto-applying normalize_triple_for_email when
sender_address is present (AGE-13691).

Validates that POSTing /broker/check with sender_address + a paraphrased
(service, problem_type, resource) triple yields the SAME fingerprint as
another POST with a different paraphrased triple but same sender_address.
This is what end-to-end closes the One Medical DM storm class of bug.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from services.broker.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BROKER_LEDGER_PATH", str(tmp_path / "ledger.json"))
    monkeypatch.setenv("PAPERCLIP_BOARD_KEY", "test-key")
    app = create_app()
    return TestClient(app)


def _post(client, payload):
    return client.post(
        "/broker/check",
        json=payload,
        headers={"Authorization": "Bearer test-key"},
    )


def test_same_sender_different_paraphrases_yield_same_fingerprint(client):
    """Two POSTs with different raw triples but same sender_address (known sender)
    should produce the same fingerprint after auto-normalize."""
    r1 = _post(client, {
        "service": "email-triage",
        "problem_type": "activation_reminder",
        "resource": "one_medical_benefit_VOLLXOM",
        "canonical_name": "One Medical sent an activation reminder",
        "sender_address": "reminders@onemedical.com",
        "subject": "One Medical sent an activation reminder",
        "business": "personal",
        "category": "benefits",
        "dry_run": True,
    })
    r2 = _post(client, {
        "service": "email-triage",
        "problem_type": "membership_activation",
        "resource": "onemedical_health_benefit",
        "canonical_name": "Activate Weekend One Medical health benefit",
        "sender_address": "support@onemedical.com",
        "subject": "Activate Weekend One Medical health benefit",
        "business": "personal",
        "category": "benefits",
        "dry_run": True,
    })
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json()["fingerprint"] == r2.json()["fingerprint"]


def test_subdomain_sender_collapses_to_parent_domain(client):
    """notifications.onemedical.com should match onemedical.com via subdomain walk."""
    r1 = _post(client, {
        "service": "email-triage",
        "problem_type": "activation",
        "resource": "one_medical",
        "sender_address": "reminders@onemedical.com",
        "subject": "Activate",
        "business": "personal",
        "category": "benefits",
        "dry_run": True,
    })
    r2 = _post(client, {
        "service": "email-triage",
        "problem_type": "anything_paraphrased",
        "resource": "something_else",
        "sender_address": "onemedical@notifications.onemedical.com",
        "subject": "Different subject paraphrase",
        "business": "personal",
        "category": "benefits",
        "dry_run": True,
    })
    assert r1.json()["fingerprint"] == r2.json()["fingerprint"]


def test_no_sender_address_does_not_auto_normalize(client):
    """When sender_address is absent, broker passes triple through unchanged."""
    r1 = _post(client, {
        "service": "age",
        "problem_type": "build_failure",
        "resource": "smoke_test_a",
        "business": "age",
        "category": "ops",
        "dry_run": True,
    })
    r2 = _post(client, {
        "service": "age",
        "problem_type": "build_failure",
        "resource": "smoke_test_b",
        "business": "age",
        "category": "ops",
        "dry_run": True,
    })
    # different resources → different fingerprints (no auto-collapse without sender)
    assert r1.json()["fingerprint"] != r2.json()["fingerprint"]


def test_regression_personal_lane_still_routes_to_general(client):
    """AGE-13645 routing must still work after AGE-13691."""
    r = _post(client, {
        "service": "email-triage",
        "problem_type": "activation",
        "resource": "one_medical",
        "sender_address": "reminders@onemedical.com",
        "subject": "Activate",
        "business": "personal",
        "category": "benefits",
        "dry_run": True,
    })
    assert r.json()["resolved_channel"] == "C0GENERAL"
