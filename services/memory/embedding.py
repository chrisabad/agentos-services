"""Async client for the local embedding server (port 8001).

API is OpenAI-compatible: POST /v1/embeddings with {model, input}.
"""

from __future__ import annotations

import os
from typing import Sequence

import httpx

DEFAULT_URL = os.environ.get("AGENTOS_EMBEDDING_URL", "http://127.0.0.1:8001")
DEFAULT_MODEL = os.environ.get("AGENTOS_EMBEDDING_MODEL", "embeddinggemma-300M")


class EmbeddingClient:
    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        model: str = DEFAULT_MODEL,
        timeout_s: float = 5.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout_s)

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

        resp = await self._client.post(
            "/v1/embeddings",
            json={"model": self.model, "input": payload_input},
        )
        resp.raise_for_status()
        data = resp.json()
        # OpenAI-compatible: {data: [{embedding: [...], index: 0}, ...]}
        items = data.get("data") or []
        # Sort by index to preserve input order
        items_sorted = sorted(items, key=lambda x: x.get("index", 0))
        return [item["embedding"] for item in items_sorted]

    async def health(self) -> bool:
        try:
            r = await self._client.get("/healthcheck", timeout=2.0)
            return r.status_code == 200 and (r.json().get("status") == "healthy")
        except Exception:
            return False
