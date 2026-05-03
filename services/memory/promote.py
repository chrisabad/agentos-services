"""Memory promotion: write to MEMORY.md AND best-effort Graphiti node.

Phase 0.2 implements the simple direct-promote path. The full dreaming-replay logic
(score thresholds, candidate-id resolution, deduplication against prior promotions)
is deferred to a later phase — `/promote` and `/append` are functionally similar
in this iteration, with `/promote` adding a Graphiti write on top.
"""

from __future__ import annotations

from services.memory.graphiti import GraphitiClient
from services.memory.models import (
    AppendRequest,
    AppendResponse,
    PromoteRequest,
    PromoteResponse,
)
from services.memory.store import append_entry


async def append_memory(req: AppendRequest) -> AppendResponse:
    memory_id, path = append_entry(
        agent=req.agent,
        text=req.text,
        kind=req.kind,
        source=req.source,
    )
    return AppendResponse(
        memory_id=memory_id,
        agent=req.agent,
        written_path=str(path),
    )


async def promote_memory(
    req: PromoteRequest,
    graphiti_client: GraphitiClient | None = None,
) -> PromoteResponse:
    memory_id, path = append_entry(
        agent=req.agent,
        text=req.text,
        kind="dreaming",
        source=req.source,
    )

    graphiti_uuid: str | None = None
    if graphiti_client:
        summary = req.text[:1500]
        name = req.text[:120].splitlines()[0] if req.text else memory_id
        try:
            graphiti_uuid = await graphiti_client.add_entity_node(
                group_id=f"agent:{req.agent}",
                name=name,
                summary=summary,
            )
        except Exception:
            graphiti_uuid = None

    return PromoteResponse(
        memory_id=memory_id,
        agent=req.agent,
        written_path=str(path),
        graphiti_node_uuid=graphiti_uuid,
    )
