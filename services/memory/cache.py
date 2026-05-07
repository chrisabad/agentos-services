"""LRU cache for embedding rerank results.

Stores embedding vectors + rerank scores keyed by (agent, query_hash) to avoid
redundant embedding calls on repeated queries.
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass, field


@dataclass
class RerankedCacheEntry:
    """Cached rerank result for a query."""

    query_hash: str
    agent_id: str
    embedding_vector: list[float]  # query embedding
    doc_embeddings: dict[int, list[float]]  # {entry_id: vector}
    rerank_scores: dict[int, float]  # {entry_id: blended_score}
    timestamp: float = field(default_factory=time.time)
    quality_score: float = 0.0  # top-3 hit rate or similar
    source_embedding_model: str = "gemma-300m"  # for invalidation on model version change


class RerankedCache:
    """Simple LRU cache for embedding rerank results."""

    def __init__(self, maxsize: int = 10000, ttl_s: float = 300):
        """Initialize cache.

        Args:
            maxsize: Maximum number of entries before LRU eviction
            ttl_s: Time-to-live in seconds (default 5 minutes)
        """
        self.maxsize = maxsize
        self.ttl_s = ttl_s
        self._cache: OrderedDict[tuple[str, str], RerankedCacheEntry] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def _make_key(self, agent_id: str, query: str) -> tuple[str, str]:
        """Create a cache key from agent and query."""
        query_hash = hashlib.sha256(query.encode()).hexdigest()[:16]
        return (agent_id, query_hash)

    def get(
        self, agent_id: str, query: str, embedding_model: str = "gemma-300m"
    ) -> RerankedCacheEntry | None:
        """Retrieve entry from cache if present, valid, and not stale.

        Returns None if not found, expired, or model version mismatch.
        """
        key = self._make_key(agent_id, query)
        entry = self._cache.get(key)

        if entry is None:
            self._misses += 1
            return None

        # Check TTL
        age_s = time.time() - entry.timestamp
        if age_s > self.ttl_s:
            del self._cache[key]
            self._misses += 1
            return None

        # Check model version
        if entry.source_embedding_model != embedding_model:
            del self._cache[key]
            self._misses += 1
            return None

        # Cache hit: move to end (most recently used)
        self._cache.move_to_end(key)
        self._hits += 1
        return entry

    def put(self, entry: RerankedCacheEntry) -> None:
        """Store entry in cache. Evicts oldest entry if at capacity."""
        key = (entry.agent_id, entry.query_hash)

        # Remove if exists (will be re-added at end)
        if key in self._cache:
            del self._cache[key]

        self._cache[key] = entry

        # LRU eviction
        while len(self._cache) > self.maxsize:
            self._cache.popitem(last=False)  # Remove oldest (first)

    def clear(self) -> None:
        """Clear all entries."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    def stats(self) -> dict:
        """Return cache statistics."""
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        return {
            "entries": len(self._cache),
            "maxsize": self.maxsize,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(hit_rate, 4),
            "total_requests": total,
        }


# Global cache instance
_rerank_cache: RerankedCache | None = None


def init_cache(maxsize: int = 10000, ttl_s: float = 300) -> RerankedCache:
    """Initialize the global rerank cache."""
    global _rerank_cache
    _rerank_cache = RerankedCache(maxsize=maxsize, ttl_s=ttl_s)
    return _rerank_cache


def get_cache() -> RerankedCache:
    """Get the global rerank cache. Initializes if not yet created."""
    global _rerank_cache
    if _rerank_cache is None:
        _rerank_cache = RerankedCache()
    return _rerank_cache
