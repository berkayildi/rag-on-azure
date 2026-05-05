"""Unit tests for the understand node."""

from __future__ import annotations

from rag_on_azure.nodes.understand import (
    SYSTEM_PROMPT,
    QueryRewrite,
    make_understand_node,
)
from rag_on_azure.state import GraphState

from .conftest import FakeLLMClient


async def test_understand_returns_rewritten_query_partial_update() -> None:
    llm = FakeLLMClient(
        complete_responses=[QueryRewrite(rewritten_query="Client Assets Sourcebook 7")]
    )
    node = make_understand_node(llm)
    state = GraphState(question="What does CASS 7 require?", tenant_id="demo")

    update = await node(state)
    assert update == {"rewritten_query": "Client Assets Sourcebook 7"}


async def test_understand_passes_question_with_system_prompt_and_schema() -> None:
    llm = FakeLLMClient(complete_responses=[QueryRewrite(rewritten_query="rewritten")])
    node = make_understand_node(llm)
    state = GraphState(question="What is CASS 7?", tenant_id="demo")

    await node(state)

    assert len(llm.complete_calls) == 1
    call = llm.complete_calls[0]
    assert call["schema"] is QueryRewrite
    messages = call["messages"]
    assert messages[0].role == "system"
    assert messages[0].content == SYSTEM_PROMPT
    assert messages[1].role == "user"
    assert messages[1].content == "What is CASS 7?"


async def test_understand_does_not_touch_other_state_fields() -> None:
    """Phase B understand only writes rewritten_query — anything else in
    the partial update would be a regression against the spec contract."""
    llm = FakeLLMClient(complete_responses=[QueryRewrite(rewritten_query="x")])
    node = make_understand_node(llm)
    state = GraphState(question="q", tenant_id="demo")

    update = await node(state)
    assert set(update.keys()) == {"rewritten_query"}
