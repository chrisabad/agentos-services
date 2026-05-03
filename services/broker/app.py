"""Broker Service — FastAPI app factory.

Phase 1.1: skeleton — `/health` + bearer auth.
Phase 1.2: `/broker/check` — exposes the `AttentionBroker.check()` decision engine.
Phase 1.3: `/broker/{record-action,disposition,topic,standing-decisions}` (deferred).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from services.broker.broker import AttentionBroker
from services.broker.models import BrokerCheckRequest, BrokerCheckResponse
from services.memory.auth import BearerAuthMiddleware
from services.memory.config import get_version

logger = logging.getLogger("broker")


def create_app() -> FastAPI:
    broker = AttentionBroker()

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

    @app.post("/broker/check", response_model=BrokerCheckResponse)
    async def broker_check(req: BrokerCheckRequest):
        try:
            result = broker.check(
                service=req.service,
                problem_type=req.problem_type,
                resource=req.resource,
                canonical_name=req.canonical_name,
                flow=req.flow,
                consumer=req.consumer,
                business=req.business or req.service,
                category=req.category,
                related_issue_ids=req.related_issue_ids,
                context=req.context,
                surface_tier=req.surface_tier,
                dry_run=req.dry_run,
            )
        except Exception as e:
            # Fail-open semantics surfaced as 500 — caller (kaleidoscope-policy
            # step 9) treats any error as fail-open and lets the message through.
            logger.exception("broker check failed")
            raise HTTPException(status_code=500, detail=f"broker error: {e}") from e
        return BrokerCheckResponse(
            decision=result.decision,
            reason=result.reason,
            rule_id=result.rule_id,
            fingerprint=result.fingerprint,
            resolved_channel=result.resolved_channel,
            topic_state=result.topic_state,
            topic_tier=result.topic_tier,
        )

    return app


app = create_app()
