"""Memory Service — FastAPI app factory.

Phase 0.1 ships only the skeleton: /health + bearer auth on every other path.
Memory endpoints land in Phase 0.2 (AGE-12025).
"""

from __future__ import annotations

from fastapi import FastAPI

from services.memory.auth import BearerAuthMiddleware
from services.memory.config import get_settings, get_version


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AgentOS Memory Service",
        version=get_version(),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(BearerAuthMiddleware)

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "service": settings.service_name,
            "version": get_version(),
        }

    return app


app = create_app()
