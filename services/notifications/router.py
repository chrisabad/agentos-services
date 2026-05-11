"""FastAPI router for the Notification Service."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from services.notifications.dedup import find_duplicate
from services.notifications.db import get_db
from services.notifications.models import (
    FeedbackWrite,
    Notification,
    NotificationCreate,
    NotificationPatch,
    NotificationRead,
    valid_transition,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])
DbDep = Annotated[Session, Depends(get_db)]


@router.post("", response_model=NotificationRead, status_code=200)
def create_notification(req: NotificationCreate, db: DbDep):
    """Create a notification, or return the existing one if a duplicate is found within the dedup window."""
    existing = find_duplicate(db, req.fingerprint, req.dedup_window_hours)
    if existing:
        result = NotificationRead.model_validate(existing)
        result.duplicate = True
        return result

    notif = Notification(
        source=req.source,
        topic_class=req.topic_class,
        priority=req.priority,
        payload=req.payload,
        fingerprint=req.fingerprint,
    )
    db.add(notif)
    db.commit()
    db.refresh(notif)
    return NotificationRead.model_validate(notif)


@router.get("", response_model=list[NotificationRead])
def list_notifications(
    db: DbDep,
    state: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    topic_class: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    q = db.query(Notification)
    if state:
        q = q.filter(Notification.state == state)
    if priority:
        q = q.filter(Notification.priority == priority)
    if topic_class:
        q = q.filter(Notification.topic_class == topic_class)
    rows = q.order_by(Notification.created_at.desc()).limit(limit).all()
    return [NotificationRead.model_validate(r) for r in rows]


@router.patch("/{notification_id}", response_model=NotificationRead)
def update_notification(notification_id: uuid.UUID, req: NotificationPatch, db: DbDep):
    notif = db.get(Notification, notification_id)
    if notif is None:
        raise HTTPException(status_code=404, detail=f"notification {notification_id} not found")

    if not valid_transition(notif.state, req.state):
        raise HTTPException(
            status_code=422,
            detail=f"invalid transition: {notif.state} → {req.state}",
        )

    notif.state = req.state
    notif.updated_at = datetime.now(tz=timezone.utc)
    if req.juno_seen_at is not None:
        notif.juno_seen_at = req.juno_seen_at
    if req.juno_acted_at is not None:
        notif.juno_acted_at = req.juno_acted_at
    if req.chris_seen_at is not None:
        notif.chris_seen_at = req.chris_seen_at
    if req.escalated_slack_ts is not None:
        notif.escalated_slack_ts = req.escalated_slack_ts

    db.commit()
    db.refresh(notif)
    return NotificationRead.model_validate(notif)


@router.post("/{notification_id}/feedback", response_model=NotificationRead)
def write_feedback(notification_id: uuid.UUID, req: FeedbackWrite, db: DbDep):
    notif = db.get(Notification, notification_id)
    if notif is None:
        raise HTTPException(status_code=404, detail=f"notification {notification_id} not found")

    notif.feedback = {
        "sentiment": req.sentiment,
        "reason": req.reason,
        "notes": req.notes,
        "reactions": req.reactions,
        "responded_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    notif.updated_at = datetime.now(tz=timezone.utc)
    db.commit()
    db.refresh(notif)
    return NotificationRead.model_validate(notif)
