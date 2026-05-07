"""Hybrid memory search: keyword + embedding rerank + Graphiti supplement.

Step 1: load all MEMORY.md entries for the agent (file-based, fast).
Step 2: keyword score (token overlap, case-insensitive) — fast, runs first.
Step 3: check rerank cache for (agent, query) hit — if valid, blend cached rerank
       scores into keyword results (no sync embedding call, no latency penalty).
       On cache miss, return keyword-only immediately and spawn a background task
       to compute embeddings + populate the cache for the next request.
Step 4: query Graphiti and add results as separate `kind=graphiti` entries.
Step 5: sort + truncate to limit.

The synchronous request path never calls the embedding server — keyword-only is
returned immediately (~200ms p99). Background cache population ensures subsequent
requests for the same (agent, query) get the full blended result within the TTL.

Graphiti step is best-effort: failures degrade gracefully.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
from collections.abc import Iterable

from services.memory.cache import RerankedCacheEntry, get_cache
from services.memory.embedding import EmbeddingClient
from services.memory.graphiti import GraphitiClient
from services.memory.models import SearchResult
from services.memory.store import MemoryEntry, ParaPersonEntry, read_entries, read_para_people_entries

KEYWORD_TOKEN = re.compile(r"[A-Za-z0-9_]+")
KEYWORD_RERANK_TOP_N = 10  # how many keyword hits to embed for rerank (bound the latency tail)
DOC_EMBED_CHARS = 400  # truncation for doc embedding inputs — Gemma-300M is much faster on shorter inputs
DEFAULT_LIMIT = 10
EMBED_RERANK_TIMEOUT_S = float(
    os.environ.get("AGENTOS_MEMORY_EMBED_RERANK_TIMEOUT_S", "1.0")
)  # Background embed timeout = 2x this. Also used for Graphiti timeout.
# No longer used for sync embedding calls — the request path returns keyword-only
# immediately and populates cache in the background.


def _query_hash(query: str) -> str:
    """Compute a short hash for the query (used in cache keys)."""
    return hashlib.sha256(query.encode()).hexdigest()[:16]


def _tokenize(text: str) -> set[str]:
    return {t.lower() for t in KEYWORD_TOKEN.findall(text)}


def _keyword_score(query_tokens: set[str], entry_tokens: set[str]) -> float:
    if not query_tokens or not entry_tokens:
        return 0.0
    overlap = query_tokens & entry_tokens
    return len(overlap) / math.sqrt(len(query_tokens) * len(entry_tokens))


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _to_result(entry: MemoryEntry, score: float) -> SearchResult:
    return SearchResult(
        source=entry.provenance or f"{entry.agent}/MEMORY.md:{entry.line_no}",
        score=round(score, 4),
        excerpt=entry.text[:600],
        kind="memory_md",
        ts=None,
    )


def _to_para_result(entry: ParaPersonEntry, score: float) -> SearchResult:
    return SearchResult(
        source=f"para_people/{entry.slug}/{entry.source_file}",
        score=round(score, 4),
        excerpt=entry.text[:600],
        kind="para_person",
        ts=None,
    )


async def _populate_rerank_cache(
    *,
    agent: str,
    query: str,
    rerank_pool: list[tuple[str, float, float]],
    embedding_client: EmbeddingClient,
) -> None:
    """Background task: compute embeddings and cache rerank results for future requests.

    Fire-and-forget. Failures are logged but don't impact the caller.
    """
    try:
        if not rerank_pool or not embedding_client:
            return

        qhash = _query_hash(query)
        inputs = [query] + [text[:DOC_EMBED_CHARS] for text, _, _ in rerank_pool]

        # Embed with a longer timeout since this is background (no caller waiting)
        vectors = await asyncio.wait_for(
            embedding_client.embed(inputs), timeout=EMBED_RERANK_TIMEOUT_S * 2
        )

        if not vectors or len(vectors) < 2:
            return

        # Compute rerank scores
        qv = vectors[0]
        rerank_scores_out: dict[int, float] = {}
        doc_embeddings: dict[int, list[float]] = {}

        for i, ((text, ks, _), candidate_vec) in enumerate(zip(rerank_pool, vectors[1:])):
            cos = _cosine(qv, candidate_vec)
            blended = 0.4 * ks + 0.6 * cos
            rerank_scores_out[i] = blended
            doc_embeddings[i] = candidate_vec

        # Store in cache
        cache_entry = RerankedCacheEntry(
            query_hash=qhash,
            agent_id=agent,
            embedding_vector=qv,
            doc_embeddings=doc_embeddings,
            rerank_scores=rerank_scores_out,
            quality_score=0.0,  # Could compute top-3 hit rate here
            source_embedding_model=embedding_client.model,
        )
        get_cache().put(cache_entry)

    except (TimeoutError, asyncio.TimeoutError, Exception):
        # Background task failure is non-critical — cache is an optimization
        pass


async def search_memory(
    *,
    agent: str,
    query: str,
    limit: int = DEFAULT_LIMIT,
    embedding_client: EmbeddingClient | None = None,
    graphiti_client: GraphitiClient | None = None,
    graphiti_group_ids: Iterable[str] | None = None,
) -> list[SearchResult]:
    """Run hybrid retrieval. Returns scored results, highest first."""
    if not query.strip():
        return []

    query_tokens = _tokenize(query)
    entries = read_entries(agent)

    # Step 1+2: keyword scoring for MEMORY.md entries
    # Each tuple carries the entry reference so sorting doesn't break alignment.
    scored: list[tuple[str, float, float, MemoryEntry | ParaPersonEntry]] = []

    for entry in entries:
        ks = _keyword_score(query_tokens, _tokenize(entry.text))
        scored.append((entry.text, ks, ks, entry))

    # Step 1b: keyword scoring for PARA people entries
    para_entries = read_para_people_entries()
    for pentry in para_entries:
        ks = _keyword_score(query_tokens, _tokenize(pentry.text))
        scored.append((pentry.text, ks, ks, pentry))

    # Step 2.5: check rerank cache for (agent, query) hit
    # If valid cache entry exists, use cached embeddings (no sync call, no latency penalty).
    cache_hit = False
    if embedding_client:
        cache_entry = get_cache().get(agent, query, embedding_client.model)
        if cache_entry is not None:
            # Cache hit: blend cached rerank scores with keyword scores
            rerank_pool = sorted(scored, key=lambda r: r[1], reverse=True)[:KEYWORD_RERANK_TOP_N]
            if rerank_pool and cache_entry.rerank_scores:
                pool_texts = {id(r[0]) for r in rerank_pool}
                new_scored: list[tuple[str, float, float, MemoryEntry | ParaPersonEntry]] = []
                pool_idx = 0
                for text, ks, _blended, entry in scored:
                    if id(text) in pool_texts:
                        new_blended = cache_entry.rerank_scores.get(pool_idx, ks)
                        new_scored.append((text, ks, new_blended, entry))
                        pool_idx += 1
                    else:
                        new_scored.append((text, ks, _blended, entry))
                scored = new_scored
                cache_hit = True

    # Step 3: On cache miss, spawn background embedding rerank (fire-and-forget).
    # The synchronous path never calls the embedding server — keyword-only is returned
    # immediately (~200ms p99). Background cache population for next request.
    rerank_pool_for_cache = sorted(scored, key=lambda r: r[1], reverse=True)[:KEYWORD_RERANK_TOP_N]
    if embedding_client and rerank_pool_for_cache and not cache_hit:
        # _populate_rerank_cache expects 3-tuples (text, keyword_score, blended_score)
        rerank_3tuples = [(t, ks, bl) for t, ks, bl, _e in rerank_pool_for_cache]
        asyncio.create_task(
            _populate_rerank_cache(
                agent=agent,
                query=query,
                rerank_pool=rerank_3tuples,
                embedding_client=embedding_client,
            )
        )

    # Sort by blended score
    scored.sort(key=lambda r: r[2], reverse=True)

    # Convert scored entries to SearchResult, dispatching on entry type.
    md_results: list[SearchResult] = []
    for text, ks, blended, entry in scored:
        if blended <= 0:
            continue
        if isinstance(entry, ParaPersonEntry):
            md_results.append(_to_para_result(entry, blended))
        else:
            md_results.append(_to_result(entry, blended))
        if len(md_results) >= limit:
            break

    # Step 4: Graphiti supplement (best-effort, bounded).
    if graphiti_client:
        try:
            facts = await asyncio.wait_for(
                graphiti_client.search(
                    query=query,
                    group_ids=list(graphiti_group_ids) if graphiti_group_ids else None,
                    max_facts=min(5, limit),
                ),
                timeout=EMBED_RERANK_TIMEOUT_S,
            )
            for f in facts:
                md_results.append(
                    SearchResult(
                        source=f"graphiti:{f.uuid}",
                        score=round(min(1.0, max(0.0, f.score)), 4),
                        excerpt=f.fact[:600],
                        kind="graphiti",
                        ts=None,
                    )
                )
        except Exception:
            pass

    md_results.sort(key=lambda r: r.score, reverse=True)
    final_results = md_results[:limit]

    return final_results
