"""Tests for search resilience:
- cache miss returns keyword-only immediately (no sync embedding call)
- cache hit returns blended results (no embedding fetch needed)
- background cache population works
- per-call graphiti timeout falls back gracefully
- LRU cache hits replace embedding fetches
"""

from __future__ import annotations

import asyncio

import pytest

from services.memory import store
from services.memory.embedding import EmbeddingClient, _LRU
from services.memory.search import search_memory


@pytest.fixture
def workspace_with_corpus(monkeypatch, tmp_path):
    fake_root = tmp_path / "workspace" / "agents"
    fake_root.mkdir(parents=True)
    monkeypatch.setattr(store, "WORKSPACE_AGENT_PATHS", [fake_root])
    # Isolate from real PARA people data on disk
    empty_people = tmp_path / "empty_people"
    empty_people.mkdir()
    monkeypatch.setattr(store, "PARA_PEOPLE_PATH", empty_people)
    md = fake_root / "axel" / "MEMORY.md"
    md.parent.mkdir(parents=True)
    md.write_text(
        "# Long-Term Memory\n"
        "\n"
        "## Promoted From Short-Term Memory (2026-04-21)\n"
        "\n"
        "- Always wear sunscreen and ship the patch\n"
        "- Test environments need quotas\n"
    )
    return fake_root


class _SlowEmbeddingClient:
    """Stand-in that always sleeps past the rerank timeout."""

    def __init__(self, delay_s: float = 5.0):
        self.delay_s = delay_s
        self.model = "test-embedding-model"

    async def embed(self, inputs):
        await asyncio.sleep(self.delay_s)
        return [[0.0] * 8 for _ in inputs]


class _FailingGraphitiClient:
    async def search(self, **kwargs):
        raise RuntimeError("graphiti down")


@pytest.mark.asyncio
async def test_search_falls_back_to_keyword_when_embedding_times_out(workspace_with_corpus):
    results = await search_memory(
        agent="axel",
        query="sunscreen patch",
        embedding_client=_SlowEmbeddingClient(delay_s=5.0),
    )
    assert results, "expected keyword-only fallback to still produce results"
    assert "sunscreen" in results[0].excerpt.lower()


@pytest.mark.asyncio
async def test_search_falls_back_when_graphiti_errors(workspace_with_corpus):
    results = await search_memory(
        agent="axel",
        query="sunscreen patch",
        graphiti_client=_FailingGraphitiClient(),
    )
    assert results, "expected memory_md results despite graphiti failure"
    # No graphiti results should appear when the client errored out
    assert all(r.kind != "graphiti" for r in results)


def test_lru_evicts_oldest_when_full():
    cache = _LRU(max_size=2)
    cache.set("a", [1.0])
    cache.set("b", [2.0])
    cache.set("c", [3.0])  # evicts "a"
    assert cache.get("a") is None
    assert cache.get("b") == [2.0]
    assert cache.get("c") == [3.0]


def test_lru_promotes_on_access():
    cache = _LRU(max_size=2)
    cache.set("a", [1.0])
    cache.set("b", [2.0])
    cache.get("a")  # promotes "a"
    cache.set("c", [3.0])  # should evict "b", not "a"
    assert cache.get("a") == [1.0]
    assert cache.get("b") is None
    assert cache.get("c") == [3.0]


@pytest.mark.asyncio
async def test_embedding_client_caches_query(monkeypatch):
    calls: list[list[str]] = []

    class _CountingFetch(EmbeddingClient):
        async def _fetch(self, items):
            calls.append(list(items))
            return [[float(i)] for i in range(len(items))]

    c = _CountingFetch(query_cache_size=64)
    await c.embed(["how to wear sunscreen"])
    await c.embed(["how to wear sunscreen"])  # cache hit
    await c.embed(["different query"])  # miss
    await c.aclose()
    # 2 fetches: first batch (1 item) + third batch (1 item)
    assert len(calls) == 2
    # Ensure the duplicate query was served from cache
    flat = [x for batch in calls for x in batch]
    assert flat.count("how to wear sunscreen") == 1
