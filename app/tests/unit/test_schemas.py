"""Unit tests for the API contract Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rag_on_azure.api.schemas import Citation, RagRequest, RagResponse, TenantContext


# ---------------------------------------------------------------------------
# RagRequest
# ---------------------------------------------------------------------------


def test_rag_request_minimal_defaults_top_k_to_5() -> None:
    req = RagRequest(question="What does CASS 7 require?")
    assert req.question == "What does CASS 7 require?"
    assert req.top_k == 5


def test_rag_request_rejects_empty_question() -> None:
    with pytest.raises(ValidationError):
        RagRequest(question="")


def test_rag_request_accepts_question_at_max_length() -> None:
    req = RagRequest(question="x" * 4000)
    assert len(req.question) == 4000


def test_rag_request_rejects_question_above_max_length() -> None:
    with pytest.raises(ValidationError):
        RagRequest(question="x" * 4001)


@pytest.mark.parametrize("top_k", [1, 5, 20])
def test_rag_request_accepts_top_k_in_bounds(top_k: int) -> None:
    req = RagRequest(question="x", top_k=top_k)
    assert req.top_k == top_k


@pytest.mark.parametrize("top_k", [0, -1, 21, 100])
def test_rag_request_rejects_top_k_out_of_bounds(top_k: int) -> None:
    with pytest.raises(ValidationError):
        RagRequest(question="x", top_k=top_k)


# ---------------------------------------------------------------------------
# Citation
# ---------------------------------------------------------------------------


def _full_citation_kwargs() -> dict[str, object]:
    return {
        "chunk_id": "abc123",
        "source": "fca-cass-7",
        "section_path": "Chapter 1 / Cross-cutting",
        "score": 0.91,
    }


def test_citation_constructs_with_all_fields() -> None:
    c = Citation(**_full_citation_kwargs())  # type: ignore[arg-type]
    assert c.chunk_id == "abc123"
    assert c.source == "fca-cass-7"
    assert c.section_path == "Chapter 1 / Cross-cutting"
    assert c.score == 0.91


@pytest.mark.parametrize("missing", ["chunk_id", "source", "section_path", "score"])
def test_citation_requires_all_fields(missing: str) -> None:
    kwargs = _full_citation_kwargs()
    del kwargs[missing]
    with pytest.raises(ValidationError):
        Citation(**kwargs)  # type: ignore[arg-type]


def test_citation_score_rejects_string_coercion() -> None:
    """StrictFloat anchors the contract that a malformed upstream score
    can't silently land in the wire response as a string."""
    bad = _full_citation_kwargs() | {"score": "0.91"}
    with pytest.raises(ValidationError):
        Citation(**bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# RagResponse
# ---------------------------------------------------------------------------


def test_rag_response_metadata_defaults_to_empty_dict() -> None:
    r = RagResponse(answer="x", citations=[])
    assert r.metadata == {}


def test_rag_response_roundtrip_preserves_all_fields() -> None:
    response = RagResponse(
        answer="CASS 7 requires firms to segregate client money...",
        citations=[Citation(**_full_citation_kwargs())],  # type: ignore[arg-type]
        metadata={"retrieved_chunk_count": 5, "elapsed_ms": 320},
    )
    rebuilt = RagResponse.model_validate(response.model_dump())
    assert rebuilt == response


# ---------------------------------------------------------------------------
# TenantContext
# ---------------------------------------------------------------------------


def test_tenant_context_minimal_defaults_is_admin_false() -> None:
    ctx = TenantContext(tenant_id="demo")
    assert ctx.tenant_id == "demo"
    assert ctx.is_admin is False


def test_tenant_context_admin_true_when_explicitly_set() -> None:
    ctx = TenantContext(tenant_id="demo", is_admin=True)
    assert ctx.is_admin is True


def test_tenant_context_requires_tenant_id() -> None:
    with pytest.raises(ValidationError):
        TenantContext()  # type: ignore[call-arg]
