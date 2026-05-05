"""Unit tests for the generate node — citation contract is audit-grade."""

from __future__ import annotations

import pytest

from rag_on_azure.models import Chunk
from rag_on_azure.nodes.generate import (
    Answer,
    CitationContractError,
    make_generate_node,
)
from rag_on_azure.state import GraphState

from .conftest import FakeLLMClient


def _chunk(
    chunk_id: str,
    source: str = "fca-test",
    section: str = "sec/1",
    score: float = 0.9,
) -> Chunk:
    return Chunk(
        id=chunk_id,
        source=source,
        section_path=section,
        chunk_text=f"body of {chunk_id}",
        score=score,
        tenant_id="demo",
    )


async def test_generate_happy_path_returns_answer_and_citations() -> None:
    chunks = [_chunk("c1", source="fca-cass-7"), _chunk("c2"), _chunk("c3")]
    llm = FakeLLMClient(
        complete_responses=[
            Answer(text="CASS 7 requires...", cited_chunk_ids=["c1", "c2"])
        ]
    )
    node = make_generate_node(llm)
    state = GraphState(question="q", tenant_id="demo", retrieved_chunks=chunks)

    update = await node(state)

    assert update["answer"] == "CASS 7 requires..."
    citations = update["citations"]
    assert [c.chunk_id for c in citations] == ["c1", "c2"]
    assert citations[0].source == "fca-cass-7"
    assert citations[0].section_path == "sec/1"
    assert citations[0].score == 0.9
    assert len(llm.complete_calls) == 1


async def test_generate_retries_when_first_attempt_cites_unknown_id() -> None:
    chunks = [_chunk("c1"), _chunk("c2")]
    llm = FakeLLMClient(
        complete_responses=[
            Answer(text="bad", cited_chunk_ids=["c-fake"]),
            Answer(text="good", cited_chunk_ids=["c1"]),
        ]
    )
    node = make_generate_node(llm)
    state = GraphState(question="q", tenant_id="demo", retrieved_chunks=chunks)

    update = await node(state)

    assert update["answer"] == "good"
    assert [c.chunk_id for c in update["citations"]] == ["c1"]
    assert len(llm.complete_calls) == 2


async def test_generate_retry_uses_strict_prompt_listing_valid_ids() -> None:
    chunks = [_chunk("c1"), _chunk("c2")]
    llm = FakeLLMClient(
        complete_responses=[
            Answer(text="bad", cited_chunk_ids=["c-fake"]),
            Answer(text="good", cited_chunk_ids=["c1", "c2"]),
        ]
    )
    node = make_generate_node(llm)
    state = GraphState(question="q", tenant_id="demo", retrieved_chunks=chunks)

    await node(state)

    retry_system_prompt = llm.complete_calls[1]["messages"][0].content
    assert "c1" in retry_system_prompt
    assert "c2" in retry_system_prompt


async def test_generate_raises_citation_contract_error_after_retry_failure() -> None:
    """§7.1 mitigation. The audit-grade contract: hallucinated citations
    don't leak past two attempts."""
    chunks = [_chunk("c1")]
    llm = FakeLLMClient(
        complete_responses=[
            Answer(text="bad1", cited_chunk_ids=["c-fake"]),
            Answer(text="bad2", cited_chunk_ids=["c-also-fake"]),
        ]
    )
    node = make_generate_node(llm)
    state = GraphState(question="q", tenant_id="demo", retrieved_chunks=chunks)

    with pytest.raises(CitationContractError):
        await node(state)
    assert len(llm.complete_calls) == 2


async def test_generate_caps_at_two_llm_calls_even_on_retry() -> None:
    """No third attempt — hard cap. If the second response is also
    invalid, we raise rather than calling the LLM a third time."""
    chunks = [_chunk("c1")]
    # Three responses queued — only the first two should ever be popped.
    llm = FakeLLMClient(
        complete_responses=[
            Answer(text="bad1", cited_chunk_ids=["c-fake-1"]),
            Answer(text="bad2", cited_chunk_ids=["c-fake-2"]),
            Answer(text="never-reached", cited_chunk_ids=["c1"]),
        ]
    )
    node = make_generate_node(llm)
    state = GraphState(question="q", tenant_id="demo", retrieved_chunks=chunks)

    with pytest.raises(CitationContractError):
        await node(state)
    assert len(llm.complete_calls) == 2


async def test_generate_accepts_empty_cited_chunk_ids() -> None:
    """Empty citations list is the legitimate "the chunks don't support
    an answer" path — not a contract violation."""
    chunks = [_chunk("c1")]
    llm = FakeLLMClient(
        complete_responses=[
            Answer(
                text="The provided chunks do not support an answer.",
                cited_chunk_ids=[],
            )
        ]
    )
    node = make_generate_node(llm)
    state = GraphState(question="q", tenant_id="demo", retrieved_chunks=chunks)

    update = await node(state)
    assert update["answer"].startswith("The provided chunks do not")
    assert update["citations"] == []
    assert len(llm.complete_calls) == 1
