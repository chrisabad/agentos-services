"""Tests for the CFC feedback Block Kit templates (AGE-13742)."""

from __future__ import annotations

import json

from services.feedback import (
    feedback_buttons_block,
    feedback_context_block,
    negative_feedback_modal_notification,
    negative_feedback_modal_report,
)


# ── feedback_buttons_block ──────────────────────────────────────


def test_feedback_buttons_has_correct_structure():
    block = feedback_buttons_block("notif-uuid-123", "notification")
    assert block["type"] == "context_actions"
    elements = block["elements"]
    assert len(elements) == 1
    fb = elements[0]
    assert fb["type"] == "feedback_buttons"
    assert "positive_button" in fb
    assert "negative_button" in fb


def test_feedback_buttons_action_id_encodes_object_type():
    block = feedback_buttons_block("uuid-1", "notification")
    fb = block["elements"][0]
    assert "notification" in fb["action_id"]

    block2 = feedback_buttons_block("uuid-2", "report")
    fb2 = block2["elements"][0]
    assert "report" in fb2["action_id"]


def test_feedback_buttons_value_carries_correlation():
    """The button value must let the handler find the object."""
    block = feedback_buttons_block("notif-abc", "notification")
    pos = block["elements"][0]["positive_button"]["value"]
    neg = block["elements"][0]["negative_button"]["value"]
    assert "notif-abc" in pos
    assert "notif-abc" in neg
    assert "notification" in pos
    assert "positive" in pos
    assert "negative" in neg


def test_feedback_buttons_serializes_to_json():
    """Result must be JSON-serializable for Slack API."""
    block = feedback_buttons_block("x", "notification")
    s = json.dumps(block)
    assert "feedback_buttons" in s


# ── feedback_context_block ──────────────────────────────────────


def test_context_block_documents_reaction_menu():
    block = feedback_context_block("notif-1", "legalzoom-deadline")
    text = block["elements"][0]["text"]
    assert "✅" in text
    assert "🔇" in text
    assert "📌" in text
    assert "🚫" in text
    assert "legalzoom-deadline" in text
    assert "notif-1" in text


# ── modals ──────────────────────────────────────────────────────


def test_notification_modal_has_notification_vocabulary():
    modal = negative_feedback_modal_notification("notif-uuid-1")
    assert modal["type"] == "modal"
    # Find the reason block
    reason_block = next(b for b in modal["blocks"] if b.get("block_id") == "reason_block")
    options = reason_block["element"]["options"]
    values = {opt["value"] for opt in options}
    assert "wrong_topic" in values
    assert "wrong_channel" in values
    assert "wrong_priority" in values
    assert "handle_silently" in values
    # No report-specific values
    assert "wrong_data" not in values
    assert "stale" not in values


def test_report_modal_has_report_vocabulary():
    modal = negative_feedback_modal_report("report-uuid-1")
    assert modal["type"] == "modal"
    reason_block = next(b for b in modal["blocks"] if b.get("block_id") == "reason_block")
    options = reason_block["element"]["options"]
    values = {opt["value"] for opt in options}
    assert "wrong_data" in values
    assert "stale" in values
    assert "wrong_framing" in values
    assert "missing_context" in values
    # No notification-specific values
    assert "wrong_priority" not in values
    assert "handle_silently" not in values


def test_modal_callback_id_encodes_object():
    modal = negative_feedback_modal_notification("notif-abc")
    assert "notif-abc" in modal["callback_id"]
    assert "notification" in modal["callback_id"]


def test_modal_serializes_to_json():
    modal = negative_feedback_modal_report("r-1")
    s = json.dumps(modal)
    assert "modal" in s
    assert "wrong_data" in s
