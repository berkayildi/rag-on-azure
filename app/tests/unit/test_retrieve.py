"""Unit tests for the retrieve node."""

from __future__ import annotations

from typing import Any

from rag_on_azure.clients.search import TenantAwareSearchClient
from rag_on_azure.models import Chunk
from rag_on_azure.nodes.retrieve import make_retrieve_node
from rag_on_azure.state import GraphState

from .conftest import FakeLLMClient, FakeSearchClient


def _doc(
    chunk_id: str, tenant_id: str = "demo", source: str = "fca-test"
) -> dict[str, Any]:
    return {
        "id": chunk_id,
        "tenant_id": tenant_id,
        "source": source,
        "section_path": "sec/1",
        "chunk_text": f"body of {chunk_id}",
        "@search.score": 1.0,
    }


async def test_retrieve_uses_rewritten_query_when_present() -> None:
    fake_search = FakeSearchClient(docs=[_doc("c1")])
    search = TenantAwareSearchClient(inner=fake_search)  # type: ignore[arg-type]
    llm = FakeLLMClient()
    node = make_retrieve_node(llm, search)
    state = GraphState(
        question="orig question",
        tenant_id="demo",
        rewritten_query="rewritten",
    )

    update = await node(state)

    assert llm.embed_calls == [["rewritten"]]
    assert fake_search.search_calls[0]["search_text"] == "rewritten"
    chunks = update["retrieved_chunks"]
    assert len(chunks) == 1
    assert isinstance(chunks[0], Chunk)


async def test_retrieve_falls_back_to_question_when_no_rewrite() -> None:
    fake_search = FakeSearchClient(docs=[_doc("c1")])
    search = TenantAwareSearchClient(inner=fake_search)  # type: ignore[arg-type]
    llm = FakeLLMClient()
    node = make_retrieve_node(llm, search)
    state = GraphState(question="orig", tenant_id="demo")

    await node(state)

    assert llm.embed_calls == [["orig"]]
    assert fake_search.search_calls[0]["search_text"] == "orig"


async def test_retrieve_filter_isolates_tenant() -> None:
    """The §5.3 cross-tenant invariant exercised through the node:
    docs from other tenants must not leak into retrieved_chunks."""
    fake_search = FakeSearchClient(
        docs=[
            _doc("c1", tenant_id="demo"),
            _doc("c2", tenant_id="other"),
            _doc("c3", tenant_id="demo"),
        ]
    )
    search = TenantAwareSearchClient(inner=fake_search)  # type: ignore[arg-type]
    llm = FakeLLMClient()
    node = make_retrieve_node(llm, search)
    state = GraphState(question="x", tenant_id="demo")

    update = await node(state)
    chunks = update["retrieved_chunks"]
    assert {c.id for c in chunks} == {"c1", "c3"}
    assert all(c.tenant_id == "demo" for c in chunks)


async def test_retrieve_uses_top_k_from_state() -> None:
    docs = [_doc(f"c{i}") for i in range(20)]
    fake_search = FakeSearchClient(docs=docs)
    search = TenantAwareSearchClient(inner=fake_search)  # type: ignore[arg-type]
    llm = FakeLLMClient()
    node = make_retrieve_node(llm, search)
    state = GraphState(question="x", tenant_id="demo", top_k=3)

    await node(state)
    assert fake_search.search_calls[0]["top"] == 3


async def test_retrieve_passes_query_vector_into_search() -> None:
    fake_search = FakeSearchClient(docs=[_doc("c1")])
    search = TenantAwareSearchClient(inner=fake_search)  # type: ignore[arg-type]
    llm = FakeLLMClient(embed_dim=1536)
    node = make_retrieve_node(llm, search)
    state = GraphState(question="x", tenant_id="demo")

    await node(state)
    vector_queries = fake_search.search_calls[0]["vector_queries"]
    assert vector_queries is not None and len(vector_queries) == 1
    assert len(vector_queries[0].vector) == 1536
