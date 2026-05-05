"""Unit tests for GraphState — the shared LangGraph state schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rag_on_azure.state import GraphState


def test_graph_state_minimal_construction_uses_defaults() -> None:
    state = GraphState(question="What does CASS 7 require?", tenant_id="demo")
    assert state.question == "What does CASS 7 require?"
    assert state.tenant_id == "demo"
    assert state.top_k == 5
    assert state.rewritten_query is None
    assert state.filters == {}
    assert state.retrieved_chunks == []
    assert state.answer is None
    assert state.citations == []
    assert state.metadata == {}


def test_graph_state_requires_question() -> None:
    with pytest.raises(ValidationError):
        GraphState(tenant_id="demo")  # type: ignore[call-arg]


def test_graph_state_requires_tenant_id() -> None:
    """The §5.3 audit-grade invariant: a state with no tenant_id is
    structurally impossible to construct."""
    with pytest.raises(ValidationError):
        GraphState(question="x")  # type: ignore[call-arg]


def test_graph_state_top_k_overridable() -> None:
    state = GraphState(question="x", tenant_id="demo", top_k=10)
    assert state.top_k == 10


def test_graph_state_metadata_accepts_arbitrary_values() -> None:
    state = GraphState(
        question="x",
        tenant_id="demo",
        metadata={"latency_ms": 320, "retrieved_chunk_count": 5},
    )
    assert state.metadata["latency_ms"] == 320
