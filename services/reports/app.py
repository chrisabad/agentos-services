"""Reports Service — FastAPI app factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from services.memory.auth import BearerAuthMiddleware
from services.memory.config import get_version
from services.reports.db import _engine
from services.reports.models import Base
from services.reports.router import router

logger = logging.getLogger("reports")


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        Base.metadata.create_all(_engine())
        yield

    app = FastAPI(
        title="AgentOS Reports Service",
        version=get_version(),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(BearerAuthMiddleware)
    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "reports", "version": get_version()}

    return app


app = create_app()
