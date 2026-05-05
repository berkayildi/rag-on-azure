"""FastAPI route handlers — health probe and the main /query endpoint.

See ``docs/design/rag-on-azure.md`` §3.4.

``/healthz`` is auth-free and dependency-free — it is the Container
App liveness probe, and any dependency on app state would defeat
the purpose. ``/readyz`` and ``/metrics`` (also §3.4) are deferred
to Day 6 / Day 7 alongside the eval-gate work.

``/query`` is the only route that touches the graph. ``tenant_id``
flows JWT → ``get_current_tenant`` → ``TenantContext`` → ``GraphState``;
the request body never carries a tenant identifier (§5.3).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from langgraph.graph.state import CompiledStateGraph

from rag_on_azure.api.schemas import RagRequest, RagResponse, TenantContext
from rag_on_azure.auth import get_current_tenant
from rag_on_azure.nodes.generate import CitationContractError
from rag_on_azure.state import GraphState

router = APIRouter()


def get_graph(request: Request) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Return the compiled graph attached to ``app.state`` by the lifespan.

    Tests override this dependency with a fake-backed graph; production
    reads the real one constructed once at startup.
    """
    return request.app.state.graph  # type: ignore[no-any-return]


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/query", response_model=RagResponse)
async def query(
    payload: RagRequest,
    ctx: Annotated[TenantContext, Depends(get_current_tenant)],
    graph: Annotated[CompiledStateGraph[Any, Any, Any, Any], Depends(get_graph)],
) -> RagResponse:
    state = GraphState(
        question=payload.question,
        tenant_id=ctx.tenant_id,
        top_k=payload.top_k,
    )
    try:
        final = await graph.ainvoke(state)
    except CitationContractError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="upstream model failed citation contract",
        ) from exc
    except ValueError as exc:
        # Today the only ValueError path through the graph is
        # TenantAwareSearchClient's tenant_id format validation
        # (a JWT carrying a malformed tenant_id like "Tenant Acme").
        # If a future node adds another ValueError path, route it
        # through its own narrow catch.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return RagResponse(
        answer=final["answer"] or "",
        citations=final["citations"],
        metadata=final.get("metadata", {}),
    )
