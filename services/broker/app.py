"""Broker Service — FastAPI app factory.

Phase 1.1: skeleton — `/health` + bearer auth. Endpoints (`/broker/check`,
`/broker/record-action`, `/broker/disposition`, `/broker/topic/<fp>`,
`/broker/standing-decisions`) land in Phase 1.2 + 1.3.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from services.memory.auth import BearerAuthMiddleware  # reuse memory's bearer middleware
from services.memory.config import get_version


def _truthy(name: str, default: bool = True) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.lower() not in ("0", "false", "no", "off", "")


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield

    app = FastAPI(
        title="AgentOS Attention Broker",
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
            "service": "broker",
            "version": get_version(),
        }

    return app


app = create_app()
