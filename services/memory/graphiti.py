"""Async client for Graphiti knowledge graph (port 8000).

Uses /search for read; /entity-node for write (Assessment-style nodes during promote).
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_URL = os.environ.get("AGENTOS_GRAPHITI_URL", "http://127.0.0.1:8000")


@dataclass
class GraphitiFact:
    uuid: str
    fact: str
    score: float
    group_id: str | None = None
    valid_at: str | None = None


class GraphitiClient:
    def __init__(self, base_url: str = DEFAULT_URL, timeout_s: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout_s)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(
        self,
        query: str,
        *,
        group_ids: list[str] | None = None,
        max_facts: int = 10,
    ) -> list[GraphitiFact]:
        """Free-text search over the graph. Returns ranked facts."""
        payload: dict[str, Any] = {"query": query, "max_facts": max_facts}
        if group_ids:
            payload["group_ids"] = group_ids
        try:
            r = await self._client.post("/search", json=payload)
        except httpx.HTTPError:
            return []
        if r.status_code >= 400:
            return []
        data = r.json()
        facts = data.get("facts") or data.get("results") or []
        out: list[GraphitiFact] = []
        for f in facts:
            out.append(
                GraphitiFact(
                    uuid=f.get("uuid", ""),
                    fact=f.get("fact") or f.get("text") or f.get("name", ""),
                    score=float(f.get("score", 0.0)),
                    group_id=f.get("group_id"),
                    valid_at=f.get("valid_at") or f.get("created_at"),
                )
            )
        return out

    async def add_entity_node(
        self,
        *,
        group_id: str,
        name: str,
        summary: str = "",
        node_uuid: str | None = None,
    ) -> str | None:
        """Create an entity node. Returns the new node uuid or None on failure."""
        u = node_uuid or str(uuid.uuid4())
        payload = {"uuid": u, "group_id": group_id, "name": name[:200], "summary": summary[:1500]}
        try:
            r = await self._client.post("/entity-node", json=payload)
        except httpx.HTTPError:
            return None
        if r.status_code >= 400:
            return None
        return u

    async def health(self) -> bool:
        try:
            r = await self._client.get("/healthcheck", timeout=2.0)
            return r.status_code == 200 and (r.json().get("status") == "healthy")
        except Exception:
            return False
