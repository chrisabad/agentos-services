"""Memory Service — FastAPI app factory.

Phase 0.1: /health + bearer auth.
Phase 0.2: /memory/{search,append,promote} reading MEMORY.md, embedding rerank
via :8001, and Graphiti supplement/write via :8000.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from services.memory.auth import BearerAuthMiddleware
from services.memory.cache import init_cache
from services.memory.config import get_settings, get_version
from services.memory.embedding import EmbeddingClient
from services.memory.graphiti import GraphitiClient
from services.memory.models import (
    AppendRequest,
    AppendResponse,
    PromoteRequest,
    PromoteResponse,
    SearchResponse,
)
from services.memory.promote import append_memory, promote_memory
from services.memory.search import search_memory
from services.memory.store import read_entries


async def _prewarm_doc_cache(client: EmbeddingClient, agents: list[str]) -> None:
    """Embed each agent's MEMORY.md entries once at startup so subsequent search
    calls hit the LRU cache for documents. Runs sequentially to respect the
    embedding server's max_concurrent=1; bounded by the configured cache size."""
    for agent in agents:
        try:
            entries = read_entries(agent)
            if not entries:
                continue
            # Match the truncation used at query time so prewarm cache keys hit
            from services.memory.search import DOC_EMBED_CHARS

            texts = [e.text[:DOC_EMBED_CHARS] for e in entries]
            # Embed in small batches to avoid one-shot huge requests
            batch_size = 16
            for i in range(0, len(texts), batch_size):
                await client.embed(texts[i : i + batch_size])
        except Exception:
            # Pre-warm is best-effort; failures shouldn't block startup
            continue


def _truthy(name: str, default: bool = True) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.lower() not in ("0", "false", "no", "off", "")


def create_app() -> FastAPI:
    settings = get_settings()
    embedding_enabled = _truthy("AGENTOS_MEMORY_EMBEDDING_ENABLED", True)
    graphiti_enabled = _truthy("AGENTOS_MEMORY_GRAPHITI_ENABLED", True)
    embedding_client = EmbeddingClient() if embedding_enabled else None
    graphiti_client = GraphitiClient() if graphiti_enabled else None

    prewarm_agents_env = os.environ.get("AGENTOS_MEMORY_PREWARM_AGENTS", "")
    prewarm_agents = [a.strip() for a in prewarm_agents_env.split(",") if a.strip()]

    # Cache settings for rerank result caching
    rerank_cache_ttl_s = float(os.environ.get("AGENTOS_MEMORY_RERANK_CACHE_TTL_S", "300"))
    rerank_cache_maxsize = int(os.environ.get("AGENTOS_MEMORY_RERANK_CACHE_MAXSIZE", "10000"))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Initialize rerank cache
        init_cache(maxsize=rerank_cache_maxsize, ttl_s=rerank_cache_ttl_s)

        if embedding_client and prewarm_agents:
            await _prewarm_doc_cache(embedding_client, prewarm_agents)
        try:
            yield
        finally:
            if embedding_client:
                await embedding_client.aclose()
            if graphiti_client:
                await graphiti_client.aclose()

    app = FastAPI(
        title="AgentOS Memory Service",
        version=get_version(),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(BearerAuthMiddleware)

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "service": settings.service_name,
            "version": get_version(),
            "embedding_enabled": bool(embedding_client),
            "graphiti_enabled": bool(graphiti_client),
        }

    @app.get("/memory/search", response_model=SearchResponse)
    async def memory_search(
        agent: str = Query(..., min_length=1, description="Agent name (workspace dir)"),
        q: str = Query(..., min_length=1, description="Free-text query"),
        limit: int = Query(default=10, ge=1, le=50),
    ):
        results = await search_memory(
            agent=agent,
            query=q,
            limit=limit,
            embedding_client=embedding_client,
            graphiti_client=graphiti_client,
            graphiti_group_ids=[f"agent:{agent}"],
        )
        return SearchResponse(results=results, query=q, agent=agent)

    @app.post("/memory/append", response_model=AppendResponse)
    async def memory_append(req: AppendRequest):
        try:
            return await append_memory(req)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post("/memory/promote", response_model=PromoteResponse)
    async def memory_promote(req: PromoteRequest):
        try:
            return await promote_memory(req, graphiti_client=graphiti_client)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    return app


app = create_app()
