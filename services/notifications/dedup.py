"""Fingerprint-based deduplication for notifications."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from services.notifications.models import Notification


def find_duplicate(
    db: Session,
    fingerprint: str,
    window_hours: int = 24,
) -> Notification | None:
    """Return the most-recent notification matching fingerprint within window_hours, or None."""
    if window_hours <= 0:
        return None
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=window_hours)
    return (
        db.query(Notification)
        .filter(Notification.fingerprint == fingerprint, Notification.created_at >= cutoff)
        .order_by(Notification.created_at.desc())
        .first()
    )
