"""Tests for search.py — pure-keyword path (no embedding/graphiti).

Embedding + Graphiti integration is exercised in live smoke tests, not unit tests
(they depend on running services that aren't part of this repo).
"""

from __future__ import annotations

import pytest

from services.memory import store
from services.memory.search import search_memory


@pytest.fixture
def workspace_with_corpus(monkeypatch, tmp_path):
    fake_root = tmp_path / "workspace" / "agents"
    fake_root.mkdir(parents=True)
    monkeypatch.setattr(store, "WORKSPACE_AGENT_PATHS", [fake_root])
    md = fake_root / "axel" / "MEMORY.md"
    md.parent.mkdir(parents=True)
    md.write_text(
        "# Long-Term Memory\n"
        "\n"
        "## Promoted From Short-Term Memory (2026-04-21)\n"
        "\n"
        "- LiteLLM Docker container can crash and the watchdog should auto-restart it.\n"
        "- Slack hooks must be async to avoid blocking the Node event loop.\n"
        "- The Hermes-First migration completed on 2026-05-02.\n"
    )
    return fake_root


@pytest.mark.asyncio
async def test_keyword_search_returns_relevant_top_result(workspace_with_corpus):
    results = await search_memory(agent="axel", query="LiteLLM Docker watchdog restart")
    assert results, "expected at least one match"
    assert "LiteLLM" in results[0].excerpt
    assert results[0].score > 0


@pytest.mark.asyncio
async def test_search_empty_query_returns_empty(workspace_with_corpus):
    results = await search_memory(agent="axel", query="   ")
    assert results == []


@pytest.mark.asyncio
async def test_search_unknown_agent_returns_empty(workspace_with_corpus):
    results = await search_memory(agent="ghost", query="anything")
    assert results == []


@pytest.mark.asyncio
async def test_search_respects_limit(workspace_with_corpus):
    results = await search_memory(agent="axel", query="the", limit=2)
    assert len(results) <= 2
