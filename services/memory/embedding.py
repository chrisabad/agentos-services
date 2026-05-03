"""Async client for the local embedding server (port 8001).

API is OpenAI-compatible: POST /v1/embeddings with {model, input}.

Includes an LRU cache keyed on the input text — query embeddings repeat across
search calls and the upstream server is sequential (max_concurrent=1), so caching
removes the largest source of search-path latency. Document embeddings (longer,
more numerous, less repetitive) are NOT cached.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from typing import Sequence

import httpx

DEFAULT_URL = os.environ.get("AGENTOS_EMBEDDING_URL", "http://127.0.0.1:8001")
DEFAULT_MODEL = os.environ.get("AGENTOS_EMBEDDING_MODEL", "embeddinggemma-300M")
DEFAULT_QUERY_CACHE_SIZE = int(os.environ.get("AGENTOS_EMBEDDING_QUERY_CACHE_SIZE", "256"))


class _LRU:
    """Tiny LRU keyed on string input."""

    def __init__(self, max_size: int):
        self.max_size = max(0, max_size)
        self._store: OrderedDict[str, list[float]] = OrderedDict()

    def get(self, key: str) -> list[float] | None:
        if key not in self._store:
            return None
        self._store.move_to_end(key)
        return self._store[key]

    def set(self, key: str, value: list[float]) -> None:
        if self.max_size <= 0:
            return
        if key in self._store:
            self._store.move_to_end(key)
            self._store[key] = value
            return
        self._store[key] = value
        while len(self._store) > self.max_size:
            self._store.popitem(last=False)

    def __len__(self) -> int:
        return len(self._store)


class EmbeddingClient:
    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        model: str = DEFAULT_MODEL,
        timeout_s: float = 30.0,
        query_cache_size: int = DEFAULT_QUERY_CACHE_SIZE,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout_s)
        self._query_cache = _LRU(query_cache_size)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def embed(self, inputs: str | Sequence[str]) -> list[list[float]]:
        """Return one vector per input. Single-string input returns a 1-element list."""
        if isinstance(inputs, str):
            payload_input: list[str] = [inputs]
        else:
            payload_input = list(inputs)
        if not payload_input:
            return []

        return await self._embed_with_cache(payload_input)

    async def embed_query(self, query: str) -> list[float] | None:
        """Single-query path with explicit cache hit — returns None on failure (caller can degrade)."""
        if not query.strip():
            return None
        cached = self._query_cache.get(query)
        if cached is not None:
            return cached
        try:
            vectors = await self._fetch([query])
        except Exception:
            return None
        if not vectors:
            return None
        self._query_cache.set(query, vectors[0])
        return vectors[0]

    async def _embed_with_cache(self, items: list[str]) -> list[list[float]]:
        """Embed a batch, using cache for any items that are queries we've seen."""
        out: list[list[float] | None] = [None] * len(items)
        misses: list[tuple[int, str]] = []
        for i, text in enumerate(items):
            cached = self._query_cache.get(text)
            if cached is not None:
                out[i] = cached
            else:
                misses.append((i, text))
        if misses:
            fresh = await self._fetch([text for _, text in misses])
            for (i, text), vec in zip(misses, fresh):
                out[i] = vec
                # Cache all inputs — query embeddings repeat (search.search_memory),
                # document embeddings repeat across queries against the same agent corpus,
                # and a 768-dim float vector is ~3 KB. 256 entries → ~768 KB, fine.
                self._query_cache.set(text, vec)
        return [v if v is not None else [] for v in out]

    async def _fetch(self, items: list[str]) -> list[list[float]]:
        resp = await self._client.post(
            "/v1/embeddings",
            json={"model": self.model, "input": items},
        )
        resp.raise_for_status()
        data = resp.json()
        api_items = data.get("data") or []
        api_items_sorted = sorted(api_items, key=lambda x: x.get("index", 0))
        return [item["embedding"] for item in api_items_sorted]

    def cache_size(self) -> int:
        return len(self._query_cache)

    async def health(self) -> bool:
        try:
            r = await self._client.get("/healthcheck", timeout=2.0)
            return r.status_code == 200 and (r.json().get("status") == "healthy")
        except Exception:
            return False
