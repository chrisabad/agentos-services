"""Smoke tests for the memory service skeleton (Phase 0.1)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.memory.app import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PAPERCLIP_BOARD_KEY", "test-token")
    # Reset cached settings so env changes apply
    from services.memory import config

    config.get_settings.cache_clear()
    return TestClient(create_app())


def test_health_returns_200_without_auth(client):
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["service"] == "memory"
    assert "version" in body


def test_unknown_path_requires_auth(client):
    res = client.get("/memory/search")
    assert res.status_code == 401


def test_bearer_with_correct_token_passes_middleware(client):
    # Endpoint not implemented yet → expect 404 (not 401), proving auth gate let it through
    res = client.get("/memory/search", headers={"authorization": "Bearer test-token"})
    assert res.status_code == 404


def test_bearer_with_wrong_token_returns_401(client):
    res = client.get("/memory/search", headers={"authorization": "Bearer wrong"})
    assert res.status_code == 401


def test_no_token_env_returns_503(monkeypatch):
    monkeypatch.delenv("PAPERCLIP_BOARD_KEY", raising=False)
    from services.memory import config

    config.get_settings.cache_clear()
    client = TestClient(create_app())
    res = client.get("/memory/search", headers={"authorization": "Bearer anything"})
    assert res.status_code == 503
