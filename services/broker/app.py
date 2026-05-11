"""Broker Service — FastAPI app factory.

Phase 1.1: skeleton — `/health` + bearer auth.
Phase 1.2: `/broker/check` — exposes the `AttentionBroker.check()` decision engine.
Phase 1.3: `/broker/{record-action,disposition,topic,standing-decisions,stats}`.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from services.broker.broker import AttentionBroker
from services.broker.fingerprint import compute_fingerprint, normalize_triple_for_email
from services.broker.ledger import get_topic, load_ledger
from services.broker.models import (
    BrokerCheckRequest,
    BrokerCheckResponse,
    DispositionRequest,
    RecordActionRequest,
    SimpleAck,
    StandingDecisionsResponse,
    StatsResponse,
    TopicLookupRequest,
    TopicSummary,
)
from services.broker.rules import DEFAULT_RULES
from services.memory.auth import BearerAuthMiddleware
from services.memory.config import get_version

logger = logging.getLogger("broker")


def _topic_summary(topic: dict) -> TopicSummary:
    return TopicSummary(
        fingerprint=topic.get("fingerprint", ""),
        canonical_name=topic.get("canonical_name", ""),
        state=topic.get("state", "unknown"),
        surface_tier=topic.get("surface_tier", "immediate"),
        surface_count=int(topic.get("surface_count", 0)),
        last_surfaced=topic.get("last_surfaced"),
        disposition=topic.get("disposition"),
        related_issue_ids=list(topic.get("related_issue_ids") or []),
        producer_actions=list(topic.get("producer_actions") or []),
        resolved_channel=topic.get("resolved_channel"),
    )


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
        return {"status": "ok", "service": "broker", "version": get_version()}

    @app.post("/broker/check", response_model=BrokerCheckResponse)
    async def broker_check(req: BrokerCheckRequest):
        # AGE-13691: when sender_address (or subject) is provided, auto-apply
        # normalize_triple_for_email so callers can pass raw email metadata and
        # the broker handles fingerprint stability. Known senders collapse to
        # canonical triples via SENDER_TRIPLE_MAP; unknown senders pass through
        # slugified deterministically.
        if req.sender_address or req.subject:
            service, problem_type, resource = normalize_triple_for_email(
                service=req.service,
                problem_type=req.problem_type,
                resource=req.resource,
                sender_address=req.sender_address,
                subject=req.subject,
            )
        else:
            service, problem_type, resource = req.service, req.problem_type, req.resource
        try:
            result = broker.check(
                service=service,
                problem_type=problem_type,
                resource=resource,
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

    @app.post("/broker/record-action", response_model=SimpleAck)
    async def broker_record_action(req: RecordActionRequest):
        fp = compute_fingerprint(req.service, req.problem_type, req.resource)
        ok = broker.record_action(req.service, req.problem_type, req.resource, req.action, req.evidence_ref)
        return SimpleAck(
            ok=ok,
            fingerprint=fp,
            detail="recorded" if ok else "topic not found",
        )

    @app.post("/broker/disposition", response_model=SimpleAck)
    async def broker_disposition(req: DispositionRequest):
        fp = compute_fingerprint(req.service, req.problem_type, req.resource)
        if req.disposition == "acknowledged":
            ok = broker.acknowledge(req.service, req.problem_type, req.resource, source=req.source, evidence=req.evidence)
        elif req.disposition == "resolved":
            ok = broker.resolve(req.service, req.problem_type, req.resource, source=req.source, evidence=req.evidence)
        else:  # muted
            ok = broker.mute(req.service, req.problem_type, req.resource, until_iso=req.muted_until)
        return SimpleAck(
            ok=ok,
            fingerprint=fp,
            detail="updated" if ok else "topic not found",
        )

    @app.post("/broker/topic/lookup", response_model=TopicSummary | None)
    async def broker_topic_lookup(req: TopicLookupRequest):
        fp = compute_fingerprint(req.service, req.problem_type, req.resource)
        ledger = load_ledger()
        topic = get_topic(ledger, fp)
        if topic is None:
            raise HTTPException(status_code=404, detail=f"topic {fp[:16]}… not found")
        return _topic_summary(topic)

    @app.get("/broker/topic/{fingerprint}", response_model=TopicSummary)
    async def broker_topic_get(fingerprint: str):
        ledger = load_ledger()
        topic = get_topic(ledger, fingerprint)
        if topic is None:
            raise HTTPException(status_code=404, detail=f"topic {fingerprint[:16]}… not found")
        return _topic_summary(topic)

    @app.get("/broker/standing-decisions", response_model=StandingDecisionsResponse)
    async def broker_standing_decisions():
        # Phase 1.1 has rules expressed as Python callables. Surface their identity
        # + docstring so the endpoint is useful even before YAML-driven rules ship.
        rules = []
        for rule_id, fn in DEFAULT_RULES:
            rules.append({
                "rule_id": rule_id,
                "name": fn.__name__,
                "doc": (fn.__doc__ or "").strip().split("\n")[0],
            })
        return StandingDecisionsResponse(rules=rules)

    @app.get("/broker/stats", response_model=StatsResponse)
    async def broker_stats():
        s = broker.stats()
        return StatsResponse(**s)

    return app


app = create_app()
