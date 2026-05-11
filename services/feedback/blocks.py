"""Slack Block Kit templates for CFC feedback affordances (AGE-13742).

Provides four building blocks:

- `feedback_buttons_block(object_id, object_type)` — 👍/👎 context_actions block
- `feedback_context_block(object_id, topic_class)` — small italic context line
  documenting the reaction menu inline so Chris doesn't need to remember
- `negative_feedback_modal_notification(notification_id)` — modal opened when
  Chris 👎's a notification, with notification-specific reason vocabulary
- `negative_feedback_modal_report(report_id)` — same for reports, with the
  report-specific vocabulary

All functions return Block Kit dicts ready to embed in Slack API calls.
"""

from __future__ import annotations

from typing import Literal

ObjectType = Literal["notification", "report"]

# ── Notification reason vocabulary ─────────────────────────────
_NOTIF_REASONS = [
    ("wrong_topic", "Wrong topic — shouldn't have been flagged"),
    ("wrong_channel", "Wrong channel — should have gone elsewhere"),
    ("wrong_time", "Wrong time — fine topic, bad timing"),
    ("wrong_priority", "Wrong priority — should have been daily/weekly brief"),
    ("handle_silently", "Handle silently — Juno should have just handled this"),
    ("other", "Other (notes below)"),
]

# ── Report reason vocabulary ───────────────────────────────────
_REPORT_REASONS = [
    ("wrong_data", "Wrong data — facts are incorrect"),
    ("wrong_framing", "Wrong framing — narrative or conclusion is off"),
    ("stale", "Stale — based on outdated information"),
    ("too_long", "Too long — too much detail"),
    ("missing_context", "Missing context — needs more background"),
    ("other", "Other (notes below)"),
]


def feedback_buttons_block(object_id: str, object_type: ObjectType) -> dict:
    """Return a Block Kit `context_actions` block with 👍/👎 buttons.

    The `value` field on each button encodes the object ID + type so the
    block_actions handler can route the response back to the right service
    endpoint.
    """
    return {
        "type": "context_actions",
        "elements": [
            {
                "type": "feedback_buttons",
                "action_id": f"cfc_feedback:{object_type}",
                "positive_button": {
                    "text": {"type": "plain_text", "text": "👍"},
                    "value": f"{object_type}:{object_id}:positive",
                },
                "negative_button": {
                    "text": {"type": "plain_text", "text": "👎"},
                    "value": f"{object_type}:{object_id}:negative",
                },
            }
        ],
    }


def feedback_context_block(object_id: str, topic_class: str) -> dict:
    """Small italic context line documenting the emoji menu inline."""
    return {
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": (
                    f"_id: `{object_id}` · topic: `{topic_class}` · "
                    f"react: ✅ acted · 🔇 mute 1w · 📌 follow up · 🚫 never_"
                ),
            }
        ],
    }


def _reasons_to_options(reasons: list[tuple[str, str]]) -> list[dict]:
    return [
        {
            "text": {"type": "plain_text", "text": label},
            "value": value,
        }
        for value, label in reasons
    ]


def negative_feedback_modal_notification(notification_id: str) -> dict:
    """Modal opened when Chris 👎's a notification."""
    return _modal(
        title="Notification feedback",
        object_id=notification_id,
        object_type="notification",
        prompt="What was wrong with this notification?",
        reasons=_NOTIF_REASONS,
    )


def negative_feedback_modal_report(report_id: str) -> dict:
    """Modal opened when Chris 👎's a report."""
    return _modal(
        title="Report feedback",
        object_id=report_id,
        object_type="report",
        prompt="What was wrong with this report?",
        reasons=_REPORT_REASONS,
    )


def _modal(
    title: str,
    object_id: str,
    object_type: ObjectType,
    prompt: str,
    reasons: list[tuple[str, str]],
) -> dict:
    return {
        "type": "modal",
        "callback_id": f"cfc_feedback_modal:{object_type}:{object_id}",
        "title": {"type": "plain_text", "text": title},
        "submit": {"type": "plain_text", "text": "Submit"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "private_metadata": f"{object_type}:{object_id}",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{prompt}*"},
            },
            {
                "type": "input",
                "block_id": "reason_block",
                "label": {"type": "plain_text", "text": "Reason"},
                "element": {
                    "type": "radio_buttons",
                    "action_id": "reason",
                    "options": _reasons_to_options(reasons),
                },
            },
            {
                "type": "input",
                "block_id": "notes_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "Notes (optional)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "notes",
                    "multiline": True,
                    "max_length": 1000,
                },
            },
        ],
    }
