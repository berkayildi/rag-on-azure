"""End-to-end LangGraph composition test using both fakes."""

from __future__ import annotations

from typing import Any

from rag_on_azure.clients.search import TenantAwareSearchClient
from rag_on_azure.graph import build_graph
from rag_on_azure.nodes.generate import Answer
from rag_on_azure.nodes.understand import QueryRewrite
from rag_on_azure.state import GraphState

from .conftest import FakeLLMClient, FakeSearchClient


def _doc(chunk_id: str, tenant_id: str = "demo") -> dict[str, Any]:
    return {
        "id": chunk_id,
        "tenant_id": tenant_id,
        "source": "fca-cass-7",
        "section_path": "Chapter 1",
        "chunk_text": f"body of {chunk_id}",
        "@search.score": 1.0,
    }


async def test_graph_runs_understand_retrieve_generate_in_order() -> None:
    fake_search = FakeSearchClient(docs=[_doc("c1"), _doc("c2")])
    search = TenantAwareSearchClient(inner=fake_search)  # type: ignore[arg-type]
    llm = FakeLLMClient(
        complete_responses=[
            QueryRewrite(rewritten_query="Client Assets Sourcebook 7"),
            Answer(text="grounded answer", cited_chunk_ids=["c1"]),
        ]
    )

    graph = build_graph(llm, search)
    initial = GraphState(question="What does CASS 7 require?", tenant_id="demo")

    final = await graph.ainvoke(initial)

    assert final["rewritten_query"] == "Client Assets Sourcebook 7"
    assert len(final["retrieved_chunks"]) == 2
    assert final["answer"] == "grounded answer"
    assert len(final["citations"]) == 1
    assert final["citations"][0].chunk_id == "c1"

    # Two LLM calls (understand + generate happy path), one embed call (retrieve)
    assert len(llm.complete_calls) == 2
    assert len(llm.embed_calls) == 1
    # tenant filter is the first AND-clause in the OData filter
    assert fake_search.search_calls[0]["filter"].startswith("tenant_id eq 'demo'")


async def test_graph_isolates_tenant_at_retrieval() -> None:
    """End-to-end §5.3 evidence: a doc owned by tenant `other` cannot
    flow into the response when the request is for tenant `demo`."""
    fake_search = FakeSearchClient(
        docs=[_doc("c1", tenant_id="demo"), _doc("c2", tenant_id="other")]
    )
    search = TenantAwareSearchClient(inner=fake_search)  # type: ignore[arg-type]
    llm = FakeLLMClient(
        complete_responses=[
            QueryRewrite(rewritten_query="x"),
            Answer(text="answer", cited_chunk_ids=["c1"]),
        ]
    )

    graph = build_graph(llm, search)
    initial = GraphState(question="x", tenant_id="demo")
    final = await graph.ainvoke(initial)

    retrieved_ids = {c.id for c in final["retrieved_chunks"]}
    assert retrieved_ids == {"c1"}
    assert "c2" not in retrieved_ids
