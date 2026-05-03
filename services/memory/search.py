"""Hybrid memory search: keyword + embedding rerank + Graphiti supplement.

Step 1: load all MEMORY.md entries for the agent (file-based, fast).
Step 2: keyword score (token overlap, case-insensitive) — fast, runs first.
Step 3: take top N keyword candidates and embed query + candidates, compute cosine, blend with keyword.
Step 4: query Graphiti and add results as separate `kind=graphiti` entries.
Step 5: sort + truncate to limit.

Embedding and Graphiti steps are best-effort: failures degrade gracefully (keyword-only result).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable

from services.memory.embedding import EmbeddingClient
from services.memory.graphiti import GraphitiClient
from services.memory.models import SearchResult
from services.memory.store import MemoryEntry, read_entries

KEYWORD_TOKEN = re.compile(r"[A-Za-z0-9_]+")
KEYWORD_RERANK_TOP_N = 30  # how many keyword hits to embed for rerank
DEFAULT_LIMIT = 10


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

    # Step 1+2: keyword scoring
    scored: list[tuple[MemoryEntry, float, float]] = []  # (entry, keyword_score, blended_score)
    for entry in entries:
        ks = _keyword_score(query_tokens, _tokenize(entry.text))
        scored.append((entry, ks, ks))

    # Step 3: embedding rerank on top N keyword hits
    rerank_pool = sorted(scored, key=lambda r: r[1], reverse=True)[:KEYWORD_RERANK_TOP_N]
    if embedding_client and rerank_pool:
        try:
            inputs = [query] + [e.text[:1200] for e, _, _ in rerank_pool]
            vectors = await embedding_client.embed(inputs)
            if vectors and len(vectors) >= 2:
                qv = vectors[0]
                rerank_scores: list[tuple[MemoryEntry, float, float]] = []
                for (entry, ks, _), candidate_vec in zip(rerank_pool, vectors[1:]):
                    cos = _cosine(qv, candidate_vec)
                    blended = 0.4 * ks + 0.6 * cos
                    rerank_scores.append((entry, ks, blended))
                # Replace the rerank-pool slice in scored with the new blended scores.
                rerank_ids = {id(t[0]) for t in rerank_pool}
                non_rerank = [r for r in scored if id(r[0]) not in rerank_ids]
                scored = non_rerank + rerank_scores
        except Exception:
            # Embedding failure → degrade to keyword-only
            pass

    # Sort by blended score
    scored.sort(key=lambda r: r[2], reverse=True)
    md_results = [_to_result(e, s) for e, _, s in scored if s > 0][: max(0, limit)]

    # Step 4: Graphiti supplement (parallel, best-effort)
    if graphiti_client:
        try:
            facts = await graphiti_client.search(
                query=query,
                group_ids=list(graphiti_group_ids) if graphiti_group_ids else None,
                max_facts=min(5, limit),
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
    return md_results[:limit]
