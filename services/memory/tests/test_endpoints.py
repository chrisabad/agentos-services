"""End-to-end endpoint tests using FastAPI's TestClient.

External services (embedding, Graphiti) are disabled via env so the test
exercises the keyword-only path. This proves the wiring + auth + payload
contracts; live integration is verified by the smoke run.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.memory import config, store


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPERCLIP_BOARD_KEY", "test-token")
    monkeypatch.setenv("AGENTOS_MEMORY_EMBEDDING_ENABLED", "0")
    monkeypatch.setenv("AGENTOS_MEMORY_GRAPHITI_ENABLED", "0")
    fake_root = tmp_path / "workspace" / "agents"
    fake_root.mkdir(parents=True)
    monkeypatch.setattr(store, "WORKSPACE_AGENTS", fake_root)
    config.get_settings.cache_clear()
    from services.memory.app import create_app

    return TestClient(create_app())


HDR = {"authorization": "Bearer test-token"}


def test_health_reports_disabled_features(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["embedding_enabled"] is False
    assert body["graphiti_enabled"] is False


def test_append_then_search_roundtrip(client):
    r = client.post(
        "/memory/append",
        headers=HDR,
        json={"agent": "axel", "text": "Always wear sunscreen and ship the patch"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent"] == "axel"
    assert body["memory_id"]

    r = client.get("/memory/search", headers=HDR, params={"agent": "axel", "q": "sunscreen"})
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results
    assert "sunscreen" in results[0]["excerpt"].lower()


def test_append_rejects_empty_text(client):
    r = client.post("/memory/append", headers=HDR, json={"agent": "x", "text": ""})
    assert r.status_code == 422  # pydantic validation


def test_promote_writes_memory_md(client):
    r = client.post(
        "/memory/promote",
        headers=HDR,
        json={"agent": "lev", "text": "MRR pulled from Lemon Squeezy weekly"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["graphiti_node_uuid"] is None  # graphiti disabled


def test_unauth_search_returns_401(client):
    r = client.get("/memory/search", params={"agent": "x", "q": "y"})
    assert r.status_code == 401


def test_search_query_required(client):
    r = client.get("/memory/search", headers=HDR, params={"agent": "x"})
    assert r.status_code == 422
