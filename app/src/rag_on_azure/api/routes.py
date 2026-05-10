"""FastAPI route handlers — health probes and the main /query endpoint.

See ``docs/design/rag-on-azure.md`` §3.4.

``/healthz`` is auth-free and dependency-free — it is the Container
App liveness probe, and any dependency on app state would defeat
the purpose. ``/readyz`` is auth-free but dependency-touching: it
pings each runtime client once and returns 503 if any check fails,
so Container Apps' readiness probe (and any external orchestrator)
can distinguish "process alive" from "ready to serve". ``/metrics``
(also §3.4) is auth-free Prometheus exposition: counters for
``queries_total``, ``retrieval_errors_total``, ``generation_errors_total``;
histograms for ``retrieval_latency_seconds``, ``generation_latency_seconds``,
``total_request_seconds``; plus the standard process/platform/GC
collectors auto-registered by ``prometheus_client`` on import. Public
posture is the demo default — production deployments should gate the
endpoint via a network allowlist or admin-JWT bearer (see
``docs/security.md``).

``/query`` is the only route that touches the graph. ``tenant_id``
flows JWT → ``get_current_tenant`` → ``TenantContext`` → ``GraphState``;
the request body never carries a tenant identifier (§5.3).

``/ingest`` is admin-only (``tenant_admin`` JWT claim, enforced via
``get_current_admin``). The route schedules the existing
``ingest.fetch`` → ``ingest.chunk`` → ``ingest.index`` pipeline as a
FastAPI background task and returns 202 with a UUID4 ``run_id`` so
operators can grep one ingest invocation cleanly in logs and metrics.
A module-level ``asyncio.Lock`` prevents concurrent runs on the same
replica; cross-replica races are documented in AGENTS.md (the
content-hash sweep in ``ingest.index`` makes those wasteful but safe).
Tenants written are taken from the manifest (default ``demo``); the
admin's JWT authorises the action, not the destination tenant.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Annotated, Any, Protocol
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from langgraph.graph.state import CompiledStateGraph
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from rag_on_azure.api.schemas import RagRequest, RagResponse, TenantContext
from rag_on_azure.auth import get_current_admin, get_current_tenant
from rag_on_azure.metrics import (
    INGEST_CHUNKS_INDEXED_TOTAL,
    INGEST_DURATION_SECONDS,
    INGEST_RUNS_TOTAL,
    QUERIES_TOTAL,
    TOTAL_REQUEST_SECONDS,
)
from rag_on_azure.nodes.generate import CitationContractError
from rag_on_azure.settings import get_settings
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


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus exposition. Public per Phase 3 D1; production hardening
    deferred to ``docs/security.md``."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post("/query", response_model=RagResponse)
async def query(
    payload: RagRequest,
    ctx: Annotated[TenantContext, Depends(get_current_tenant)],
    graph: Annotated[CompiledStateGraph[Any, Any, Any, Any], Depends(get_graph)],
) -> RagResponse:
    started = time.perf_counter()
    state = GraphState(
        question=payload.question,
        tenant_id=ctx.tenant_id,
        top_k=payload.top_k,
    )
    try:
        final = await graph.ainvoke(state)
    except CitationContractError as exc:
        QUERIES_TOTAL.labels(tenant_id=ctx.tenant_id, status="error").inc()
        TOTAL_REQUEST_SECONDS.observe(time.perf_counter() - started)
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
        QUERIES_TOTAL.labels(tenant_id=ctx.tenant_id, status="error").inc()
        TOTAL_REQUEST_SECONDS.observe(time.perf_counter() - started)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    QUERIES_TOTAL.labels(tenant_id=ctx.tenant_id, status="success").inc()
    TOTAL_REQUEST_SECONDS.observe(time.perf_counter() - started)
    return RagResponse(
        answer=final["answer"] or "",
        citations=final["citations"],
        metadata=final.get("metadata", {}),
    )


# Module-level lock prevents concurrent ingest runs on the same replica.
# Cross-replica races exist when Container App scales >1 (documented in
# AGENTS.md); the content-hash sweep in ingest.index makes a race wasteful
# but not destructive. Acquired in the route handler, released in the
# background task's finally block — the lock outlives the HTTP response.
_ingest_lock = asyncio.Lock()


async def _run_ingest_pipeline(
    run_id: str,
    manifest_path: Path,
    cache_dir: Path,
) -> None:
    """Background task body: fetch → chunk → index, with metrics + structured
    logs keyed on ``run_id`` so operators can correlate one invocation."""
    extra = {"run_id": run_id}
    started = time.perf_counter()
    try:
        # Imports are deferred so that pulling in the ingest package's
        # transitive heavy deps (langchain-text-splitters, pypdf, tiktoken,
        # markdownify) only happens when the route fires, not at app boot.
        from azure.identity.aio import DefaultAzureCredential
        from azure.search.documents.aio import SearchClient
        from azure.search.documents.indexes.aio import SearchIndexClient

        from ingest.chunk import chunk_all
        from ingest.fetch import fetch_all
        from ingest.index import index_all
        from rag_on_azure.clients.llm import AzureOpenAIClient

        settings = get_settings()
        cache_dir.mkdir(parents=True, exist_ok=True)

        log.info("ingest %s: fetch starting", run_id, extra=extra)
        await fetch_all(manifest_path, cache_dir)

        log.info("ingest %s: chunk starting", run_id, extra=extra)
        await asyncio.to_thread(chunk_all, cache_dir)

        log.info("ingest %s: index starting", run_id, extra=extra)
        credential = DefaultAzureCredential()
        try:
            embedder = AzureOpenAIClient(
                endpoint=settings.azure_openai_endpoint,
                embedding_deployment=settings.azure_openai_embedding_deployment,
                chat_deployment=settings.azure_openai_chat_deployment,
                credential=credential,
            )
            search_index_client = SearchIndexClient(
                endpoint=settings.azure_search_endpoint, credential=credential
            )
            search_client = SearchClient(
                endpoint=settings.azure_search_endpoint,
                index_name=settings.azure_search_index_name,
                credential=credential,
            )
            try:
                summary = await index_all(
                    cache_dir,
                    embedder=embedder,
                    search_client=search_client,
                    search_index_client=search_index_client,
                )
                INGEST_CHUNKS_INDEXED_TOTAL.inc(summary["uploaded"])
                log.info(
                    "ingest %s: complete (uploaded=%d unchanged=%d)",
                    run_id,
                    summary["uploaded"],
                    summary["unchanged"],
                    extra=extra,
                )
            finally:
                await embedder.close()
                await search_client.close()
                await search_index_client.close()
        finally:
            await credential.close()

        INGEST_RUNS_TOTAL.labels(status="success").inc()
    except Exception:
        log.exception("ingest %s: failed", run_id, extra=extra)
        INGEST_RUNS_TOTAL.labels(status="error").inc()
    finally:
        INGEST_DURATION_SECONDS.observe(time.perf_counter() - started)
        _ingest_lock.release()


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest(
    background_tasks: BackgroundTasks,
    ctx: Annotated[TenantContext, Depends(get_current_admin)],
) -> dict[str, str]:
    """Admin-only ingest trigger. Schedules the corpus pipeline as a
    background task; returns 202 with a UUID4 ``run_id`` for log/metric
    correlation. 409 if a run is already in flight on this replica."""
    if _ingest_lock.locked():
        INGEST_RUNS_TOTAL.labels(status="conflict").inc()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="ingest already in progress on this replica",
        )

    # Acquire synchronously between the .locked() check and the schedule;
    # asyncio.Lock.acquire() returns immediately when free with no
    # intervening await, so no concurrent acquirer can interpose.
    await _ingest_lock.acquire()

    settings = get_settings()
    run_id = str(uuid4())
    log.info(
        "ingest %s: queued (manifest=%s cache=%s)",
        run_id,
        settings.ingest_manifest_path,
        settings.ingest_cache_dir,
        extra={"run_id": run_id},
    )
    background_tasks.add_task(
        _run_ingest_pipeline,
        run_id=run_id,
        manifest_path=settings.ingest_manifest_path,
        cache_dir=settings.ingest_cache_dir,
    )
    return {"status": "started", "run_id": run_id}
