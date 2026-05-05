"""Integration tests for the FastAPI surface.

Uses ``TestClient`` + ``dependency_overrides`` to exercise the full
request → graph → response path with the canonical
``TenantAwareSearchClient`` fronting an in-memory ``FakeSearchClient``
(so the §5.3 audit-grade tenant filter is the real boundary, not
elided), and the ``LLMClient`` Protocol substituted with
``FakeLLMClient`` queued with the responses each test needs.

The TestClient is constructed without entering its context manager so
the FastAPI lifespan does not run — tests don't need (and shouldn't
trigger) real Azure client construction.
"""

from __future__ import annotations

from typing import Any

import jwt
from fastapi.testclient import TestClient

from rag_on_azure.api.routes import get_graph
from rag_on_azure.clients.search import TenantAwareSearchClient
from rag_on_azure.graph import build_graph
from rag_on_azure.main import create_app
from rag_on_azure.nodes.generate import Answer
from rag_on_azure.nodes.understand import QueryRewrite

from ..unit.conftest import FakeLLMClient, FakeSearchClient

_DEV_KEY = "dev-only-not-a-secret-padded-32b"


def _mint(claims: dict[str, Any]) -> str:
    return jwt.encode(claims, _DEV_KEY, algorithm="HS256")


def _doc(chunk_id: str, tenant_id: str = "demo") -> dict[str, Any]:
    return {
        "id": chunk_id,
        "tenant_id": tenant_id,
        "source": "fca-cass-7",
        "section_path": "Chapter 1",
        "chunk_text": f"body of {chunk_id}",
        "@search.score": 1.0,
    }


def _wire(
    complete_responses: list[Any],
    docs: list[dict[str, Any]] | None = None,
) -> tuple[TestClient, FakeLLMClient, FakeSearchClient]:
    fake_search = FakeSearchClient(docs=docs or [])
    search_client = TenantAwareSearchClient(inner=fake_search)  # type: ignore[arg-type]
    llm = FakeLLMClient(complete_responses=complete_responses)
    graph = build_graph(llm, search_client)

    app = create_app()
    app.dependency_overrides[get_graph] = lambda: graph
    return TestClient(app), llm, fake_search


def test_healthz_returns_200() -> None:
    app = create_app()
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_query_requires_auth() -> None:
    client, _, _ = _wire(complete_responses=[], docs=[])
    response = client.post("/query", json={"question": "x"})
    assert response.status_code == 401


def test_query_returns_rag_response_with_citations() -> None:
    client, _, _ = _wire(
        complete_responses=[
            QueryRewrite(rewritten_query="Client Assets Sourcebook 7"),
            Answer(text="CASS 7 requires...", cited_chunk_ids=["c1"]),
        ],
        docs=[_doc("c1")],
    )
    token = _mint({"sub": "u", "tenant_id": "demo"})

    response = client.post(
        "/query",
        json={"question": "What does CASS 7 require?"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "CASS 7 requires..."
    assert len(body["citations"]) == 1
    assert body["citations"][0]["chunk_id"] == "c1"
    assert body["citations"][0]["source"] == "fca-cass-7"


def test_query_propagates_tenant_id_from_jwt_into_search_filter() -> None:
    """End-to-end §5.3 evidence at the API boundary: tenant_id flows
    JWT → TenantContext → GraphState → search filter, never from body."""
    client, _, fake_search = _wire(
        complete_responses=[
            QueryRewrite(rewritten_query="x"),
            Answer(text="answer", cited_chunk_ids=[]),
        ],
        docs=[_doc("c1", tenant_id="acme")],
    )
    token = _mint({"sub": "u", "tenant_id": "acme"})

    response = client.post(
        "/query",
        json={"question": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert fake_search.search_calls[0]["filter"].startswith("tenant_id eq 'acme'")


def test_query_502_on_citation_contract_error() -> None:
    """Two consecutive fabricated-citation responses (one initial + one
    retry) trip CitationContractError; the API returns 502."""
    client, _, _ = _wire(
        complete_responses=[
            QueryRewrite(rewritten_query="x"),
            Answer(text="bad1", cited_chunk_ids=["c-fake-1"]),
            Answer(text="bad2", cited_chunk_ids=["c-fake-2"]),
        ],
        docs=[_doc("c1")],
    )
    token = _mint({"sub": "u", "tenant_id": "demo"})

    response = client.post(
        "/query",
        json={"question": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "upstream model failed citation contract"


def test_query_400_on_invalid_tenant_id_in_jwt() -> None:
    """A JWT carrying a tenant_id that fails the search client's
    ``^[a-z0-9-]+$`` validation (e.g. a space) is a 400 — the request
    is malformed at the auth boundary even though the JWT itself
    decoded cleanly."""
    client, _, _ = _wire(
        complete_responses=[QueryRewrite(rewritten_query="x")],
        docs=[],
    )
    token = _mint({"sub": "u", "tenant_id": "Tenant Acme"})

    response = client.post(
        "/query",
        json={"question": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
