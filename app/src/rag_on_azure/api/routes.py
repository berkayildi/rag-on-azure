"""FastAPI route handlers — health probes and the main /query endpoint.

See ``docs/design/rag-on-azure.md`` §3.4.

``/healthz`` is auth-free and dependency-free — it is the Container
App liveness probe, and any dependency on app state would defeat
the purpose. ``/readyz`` is auth-free but dependency-touching: it
pings each runtime client once and returns 503 if any check fails,
so Container Apps' readiness probe (and any external orchestrator)
can distinguish "process alive" from "ready to serve". ``/metrics``
(also §3.4) is deferred to Day 7 alongside the eval-gate work.

``/query`` is the only route that touches the graph. ``tenant_id``
flows JWT → ``get_current_tenant`` → ``TenantContext`` → ``GraphState``;
the request body never carries a tenant identifier (§5.3).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from langgraph.graph.state import CompiledStateGraph

from rag_on_azure.api.schemas import RagRequest, RagResponse, TenantContext
from rag_on_azure.auth import get_current_tenant
from rag_on_azure.nodes.generate import CitationContractError
from rag_on_azure.state import GraphState

log = logging.getLogger(__name__)

# Per-check hard ceiling for /readyz. Long enough to absorb a single TCP
# round-trip + Azure SDK cold-path overhead; short enough that a stuck
# dependency cannot wedge the readiness probe.
READYZ_CHECK_TIMEOUT_S = 2.0

router = APIRouter()


class _Pingable(Protocol):
    """Structural contract for any client that exposes a readiness probe.

    Satisfied by ``AzureOpenAIClient`` and ``TenantAwareSearchClient`` in
    production, and by test stubs that override ``get_llm`` / ``get_search``.
    Lives here (not in ``clients/``) because it is a route-layer concern —
    no graph node calls ``ping()``.
    """

    async def ping(self) -> None: ...


def get_graph(request: Request) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Return the compiled graph attached to ``app.state`` by the lifespan.

    Tests override this dependency with a fake-backed graph; production
    reads the real one constructed once at startup.
    """
    return request.app.state.graph  # type: ignore[no-any-return]


def get_llm(request: Request) -> _Pingable:
    return request.app.state.llm  # type: ignore[no-any-return]


def get_search(request: Request) -> _Pingable:
    return request.app.state.search  # type: ignore[no-any-return]


def get_key_vault(request: Request) -> _Pingable:
    return request.app.state.key_vault  # type: ignore[no-any-return]


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


async def _run_check(name: str, client: _Pingable) -> tuple[str, str]:
    try:
        await asyncio.wait_for(client.ping(), timeout=READYZ_CHECK_TIMEOUT_S)
        return name, "ok"
    except Exception as exc:
        log.warning("readyz check %s failed: %s", name, type(exc).__name__)
        return name, f"failed: {type(exc).__name__}"


@router.get("/readyz")
async def readyz(
    llm: Annotated[_Pingable, Depends(get_llm)],
    search: Annotated[_Pingable, Depends(get_search)],
    key_vault: Annotated[_Pingable, Depends(get_key_vault)],
) -> JSONResponse:
    results = await asyncio.gather(
        _run_check("openai", llm),
        _run_check("search", search),
        _run_check("key_vault", key_vault),
    )
    checks = dict(results)
    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK
        if all_ok
        else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": "ready" if all_ok else "not_ready",
            "checks": checks,
        },
    )


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
