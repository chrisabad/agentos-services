"""Reports Service tests — SQLite in-memory, no live Postgres needed."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.reports.app import create_app
from services.reports.db import get_db
from services.reports.models import Base

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
    monkeypatch.setenv("REPORTS_DB_URL", "sqlite:///:memory:")
    from services.memory import config as memory_config
    memory_config.get_settings.cache_clear()

    Base.metadata.create_all(_TEST_ENGINE)
    app = create_app()
    app.dependency_overrides[get_db] = _override_db
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as c:
        yield c
    Base.metadata.drop_all(_TEST_ENGINE)


_REPORT = {
    "source": "juno-weekly",
    "topic_class": "legal",
    "storage_type": "notion",
    "storage_url": "https://notion.so/abc123",
    "storage_doc_id": "doc-001",
    "sources_cited": [{"title": "LegalZoom filing", "url": "https://legalzoom.com"}],
}


# ── create ────────────────────────────────────────────────────────────────────

def test_create_report(client):
    r = client.post("/reports", json=_REPORT)
    assert r.status_code == 201
    data = r.json()
    assert data["source"] == "juno-weekly"
    assert data["state"] == "drafted"
    assert data["draft_version"] == 1
    assert data["published_version"] is None


def test_create_returns_id_and_fields(client):
    r = client.post("/reports", json=_REPORT)
    data = r.json()
    assert "id" in data
    assert data["topic_class"] == "legal"
    assert data["storage_type"] == "notion"
    assert len(data["sources_cited"]) == 1


def test_create_minimal(client):
    r = client.post("/reports", json={
        "source": "auto-sweep",
        "topic_class": "ops",
        "storage_type": "paperclip_doc",
    })
    assert r.status_code == 201
    assert r.json()["storage_url"] is None


# ── list ──────────────────────────────────────────────────────────────────────

def test_list_reports_empty(client):
    r = client.get("/reports")
    assert r.status_code == 200
    assert r.json() == []


def test_list_reports_returns_created(client):
    client.post("/reports", json=_REPORT)
    r = client.get("/reports")
    assert len(r.json()) == 1


def test_list_filter_by_state(client):
    client.post("/reports", json=_REPORT)
    assert len(client.get("/reports?state=drafted").json()) == 1
    assert len(client.get("/reports?state=reviewed").json()) == 0


def test_list_filter_by_topic_class(client):
    client.post("/reports", json=_REPORT)
    client.post("/reports", json={**_REPORT, "topic_class": "financial"})
    assert len(client.get("/reports?topic_class=legal").json()) == 1
    assert len(client.get("/reports?topic_class=financial").json()) == 1


def test_list_filter_by_source(client):
    client.post("/reports", json=_REPORT)
    client.post("/reports", json={**_REPORT, "source": "axel-sweep"})
    assert len(client.get("/reports?source=juno-weekly").json()) == 1
    assert len(client.get("/reports?source=axel-sweep").json()) == 1


# ── state machine ─────────────────────────────────────────────────────────────

def test_state_drafted_to_reviewed(client):
    report_id = client.post("/reports", json=_REPORT).json()["id"]
    r = client.patch(f"/reports/{report_id}", json={"state": "reviewed"})
    assert r.status_code == 200
    assert r.json()["state"] == "reviewed"


def test_state_drafted_to_archived(client):
    report_id = client.post("/reports", json=_REPORT).json()["id"]
    r = client.patch(f"/reports/{report_id}", json={"state": "archived"})
    assert r.status_code == 200
    assert r.json()["state"] == "archived"


def test_state_reviewed_to_published(client):
    report_id = client.post("/reports", json=_REPORT).json()["id"]
    client.patch(f"/reports/{report_id}", json={"state": "reviewed"})
    r = client.patch(f"/reports/{report_id}", json={"state": "published"})
    assert r.status_code == 200
    data = r.json()
    assert data["state"] == "published"
    assert data["published_version"] == 1
    assert data["published_at"] is not None


def test_state_reviewed_back_to_drafted(client):
    report_id = client.post("/reports", json=_REPORT).json()["id"]
    client.patch(f"/reports/{report_id}", json={"state": "reviewed"})
    r = client.patch(f"/reports/{report_id}", json={"state": "drafted"})
    assert r.status_code == 200
    assert r.json()["state"] == "drafted"


def test_state_published_to_archived(client):
    report_id = client.post("/reports", json=_REPORT).json()["id"]
    client.patch(f"/reports/{report_id}", json={"state": "reviewed"})
    client.patch(f"/reports/{report_id}", json={"state": "published"})
    r = client.patch(f"/reports/{report_id}", json={"state": "archived"})
    assert r.status_code == 200
    assert r.json()["state"] == "archived"


def test_state_invalid_drafted_to_published(client):
    report_id = client.post("/reports", json=_REPORT).json()["id"]
    r = client.patch(f"/reports/{report_id}", json={"state": "published"})
    assert r.status_code == 422


def test_state_archived_is_terminal(client):
    report_id = client.post("/reports", json=_REPORT).json()["id"]
    client.patch(f"/reports/{report_id}", json={"state": "archived"})
    r = client.patch(f"/reports/{report_id}", json={"state": "drafted"})
    assert r.status_code == 422


def test_state_patch_updates_storage_fields(client):
    report_id = client.post("/reports", json=_REPORT).json()["id"]
    r = client.patch(f"/reports/{report_id}", json={
        "state": "reviewed",
        "storage_url": "https://notion.so/updated",
        "storage_doc_id": "doc-updated",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["storage_url"] == "https://notion.so/updated"
    assert data["storage_doc_id"] == "doc-updated"


def test_state_404_on_missing(client):
    r = client.patch(f"/reports/{uuid.uuid4()}", json={"state": "reviewed"})
    assert r.status_code == 404


# ── juno-review ───────────────────────────────────────────────────────────────

def test_juno_review_write_advances_to_reviewed(client):
    report_id = client.post("/reports", json=_REPORT).json()["id"]
    r = client.post(f"/reports/{report_id}/juno-review", json={
        "reviewed_by": "juno",
        "edits_summary": "Cleaned up legalese",
        "kicked_back_to": None,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["state"] == "reviewed"
    assert data["juno_review"]["reviewed_by"] == "juno"
    assert data["juno_review"]["edits_summary"] == "Cleaned up legalese"
    assert "reviewed_at" in data["juno_review"]


def test_juno_review_minimal(client):
    report_id = client.post("/reports", json=_REPORT).json()["id"]
    r = client.post(f"/reports/{report_id}/juno-review", json={"reviewed_by": "juno"})
    assert r.status_code == 200
    assert r.json()["juno_review"]["reviewed_by"] == "juno"


def test_juno_review_does_not_re_advance_already_reviewed(client):
    report_id = client.post("/reports", json=_REPORT).json()["id"]
    client.patch(f"/reports/{report_id}", json={"state": "reviewed"})
    client.patch(f"/reports/{report_id}", json={"state": "published"})
    r = client.post(f"/reports/{report_id}/juno-review", json={"reviewed_by": "juno"})
    assert r.status_code == 200
    assert r.json()["state"] == "published"


def test_juno_review_404_on_missing(client):
    r = client.post(f"/reports/{uuid.uuid4()}/juno-review", json={"reviewed_by": "juno"})
    assert r.status_code == 404


# ── feedback ──────────────────────────────────────────────────────────────────

def test_feedback_write(client):
    report_id = client.post("/reports", json=_REPORT).json()["id"]
    r = client.post(f"/reports/{report_id}/feedback", json={
        "sentiment": "positive",
        "reason": "accurate",
        "notes": "Great summary",
        "reactions": ["👍"],
    })
    assert r.status_code == 200
    fb = r.json()["feedback"]
    assert fb["sentiment"] == "positive"
    assert fb["reason"] == "accurate"
    assert "responded_at" in fb


def test_feedback_overwrite(client):
    report_id = client.post("/reports", json=_REPORT).json()["id"]
    client.post(f"/reports/{report_id}/feedback", json={"sentiment": "negative"})
    r = client.post(f"/reports/{report_id}/feedback", json={"sentiment": "positive"})
    assert r.json()["feedback"]["sentiment"] == "positive"


def test_feedback_404_on_missing(client):
    r = client.post(f"/reports/{uuid.uuid4()}/feedback", json={"sentiment": "positive"})
    assert r.status_code == 404


# ── health ────────────────────────────────────────────────────────────────────

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "reports"
