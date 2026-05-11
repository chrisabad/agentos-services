"""Notification Service tests — SQLite in-memory, no live Postgres needed."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.notifications.app import create_app
from services.notifications.db import get_db
from services.notifications.models import Base

_TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSession = sessionmaker(bind=_TEST_ENGINE, autocommit=False, autoflush=False)


def _override_db():
    db = _TestSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PAPERCLIP_BOARD_KEY", "test-token")
    monkeypatch.setenv("NOTIFICATIONS_DB_URL", "sqlite:///:memory:")
    from services.memory import config as memory_config
    memory_config.get_settings.cache_clear()

    Base.metadata.create_all(_TEST_ENGINE)
    app = create_app()
    app.dependency_overrides[get_db] = _override_db
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as c:
        yield c
    Base.metadata.drop_all(_TEST_ENGINE)


_NOTIF = {
    "source": "ops-sweep",
    "topic_class": "ops",
    "priority": "immediate",
    "payload": {"message_text": "LegalZoom filing due"},
    "fingerprint": "abc123",
    "dedup_window_hours": 24,
}


# ── create ────────────────────────────────────────────────────────────────────

def test_create_notification(client):
    r = client.post("/notifications", json=_NOTIF)
    assert r.status_code == 200
    data = r.json()
    assert data["source"] == "ops-sweep"
    assert data["state"] == "new"
    assert data["duplicate"] is False


def test_create_returns_id_and_fingerprint(client):
    r = client.post("/notifications", json=_NOTIF)
    data = r.json()
    assert "id" in data
    assert data["fingerprint"] == "abc123"


# ── dedup ─────────────────────────────────────────────────────────────────────

def test_dedup_within_window_returns_existing(client):
    r1 = client.post("/notifications", json=_NOTIF)
    r2 = client.post("/notifications", json=_NOTIF)
    assert r1.json()["id"] == r2.json()["id"]
    assert r2.json()["duplicate"] is True


def test_dedup_zero_window_always_creates_new(client):
    payload = {**_NOTIF, "dedup_window_hours": 0}
    r1 = client.post("/notifications", json=payload)
    r2 = client.post("/notifications", json=payload)
    assert r1.json()["id"] != r2.json()["id"]
    assert r2.json()["duplicate"] is False


def test_dedup_different_fingerprint_creates_new(client):
    r1 = client.post("/notifications", json=_NOTIF)
    r2 = client.post("/notifications", json={**_NOTIF, "fingerprint": "xyz999"})
    assert r1.json()["id"] != r2.json()["id"]
    assert r2.json()["duplicate"] is False


def test_dedup_outside_window_creates_new(client):
    r1 = client.post("/notifications", json=_NOTIF)
    notif_id = r1.json()["id"]

    # Backdate the first notification beyond the dedup window
    import uuid as _uuid
    db = _TestSession()
    from services.notifications.models import Notification
    notif = db.get(Notification, _uuid.UUID(notif_id))
    notif.created_at = datetime.now(tz=timezone.utc) - timedelta(hours=25)
    db.commit()
    db.close()

    r2 = client.post("/notifications", json=_NOTIF)
    assert r2.json()["id"] != notif_id
    assert r2.json()["duplicate"] is False


# ── list ──────────────────────────────────────────────────────────────────────

def test_list_notifications_empty(client):
    r = client.get("/notifications")
    assert r.status_code == 200
    assert r.json() == []


def test_list_notifications_returns_created(client):
    client.post("/notifications", json=_NOTIF)
    r = client.get("/notifications")
    assert len(r.json()) == 1


def test_list_filter_by_state(client):
    client.post("/notifications", json=_NOTIF)
    assert len(client.get("/notifications?state=new").json()) == 1
    assert len(client.get("/notifications?state=read").json()) == 0


def test_list_filter_by_topic_class(client):
    client.post("/notifications", json=_NOTIF)
    client.post("/notifications", json={**_NOTIF, "fingerprint": "fp2", "topic_class": "financial"})
    assert len(client.get("/notifications?topic_class=ops").json()) == 1
    assert len(client.get("/notifications?topic_class=financial").json()) == 1


# ── state machine ─────────────────────────────────────────────────────────────

def test_state_new_to_read(client):
    notif_id = client.post("/notifications", json=_NOTIF).json()["id"]
    r = client.patch(f"/notifications/{notif_id}", json={"state": "read"})
    assert r.status_code == 200
    assert r.json()["state"] == "read"


def test_state_new_to_escalated(client):
    notif_id = client.post("/notifications", json=_NOTIF).json()["id"]
    r = client.patch(f"/notifications/{notif_id}", json={"state": "escalated"})
    assert r.status_code == 200
    assert r.json()["state"] == "escalated"


def test_state_read_to_acted(client):
    notif_id = client.post("/notifications", json=_NOTIF).json()["id"]
    client.patch(f"/notifications/{notif_id}", json={"state": "read"})
    r = client.patch(f"/notifications/{notif_id}", json={"state": "acted"})
    assert r.status_code == 200
    assert r.json()["state"] == "acted"


def test_state_invalid_transition_new_to_acted(client):
    notif_id = client.post("/notifications", json=_NOTIF).json()["id"]
    r = client.patch(f"/notifications/{notif_id}", json={"state": "acted"})
    assert r.status_code == 422


def test_state_terminal_cannot_transition(client):
    notif_id = client.post("/notifications", json=_NOTIF).json()["id"]
    client.patch(f"/notifications/{notif_id}", json={"state": "escalated"})
    r = client.patch(f"/notifications/{notif_id}", json={"state": "read"})
    assert r.status_code == 422


def test_state_404_on_missing(client):
    import uuid
    r = client.patch(f"/notifications/{uuid.uuid4()}", json={"state": "read"})
    assert r.status_code == 404


# ── feedback ──────────────────────────────────────────────────────────────────

def test_feedback_write(client):
    notif_id = client.post("/notifications", json=_NOTIF).json()["id"]
    r = client.post(f"/notifications/{notif_id}/feedback", json={
        "sentiment": "negative",
        "reason": "wrong_channel",
        "notes": "Should go to #agent-ops",
        "reactions": ["👎"],
    })
    assert r.status_code == 200
    fb = r.json()["feedback"]
    assert fb["sentiment"] == "negative"
    assert fb["reason"] == "wrong_channel"
    assert "responded_at" in fb


def test_feedback_overwrite(client):
    notif_id = client.post("/notifications", json=_NOTIF).json()["id"]
    client.post(f"/notifications/{notif_id}/feedback", json={"sentiment": "negative"})
    r = client.post(f"/notifications/{notif_id}/feedback", json={"sentiment": "positive"})
    assert r.json()["feedback"]["sentiment"] == "positive"


def test_feedback_404_on_missing(client):
    import uuid
    r = client.post(f"/notifications/{uuid.uuid4()}/feedback", json={"sentiment": "positive"})
    assert r.status_code == 404
