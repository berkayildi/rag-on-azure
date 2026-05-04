"""Unit tests for the cross-cutting Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rag_on_azure.models import Chunk, Message


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------


def test_message_validates_role_literal() -> None:
    for role in ("system", "user", "assistant"):
        m = Message(role=role, content="hello")
        assert m.role == role
        assert m.content == "hello"


@pytest.mark.parametrize("bad_role", ["tool", "function", "developer", "", "User"])
def test_message_rejects_non_literal_roles(bad_role: str) -> None:
    with pytest.raises(ValidationError):
        Message(role=bad_role, content="x")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Chunk
# ---------------------------------------------------------------------------


def _full_chunk_kwargs() -> dict[str, object]:
    return {
        "id": "abc123",
        "source": "fca-ps22-9",
        "section_path": "Chapter 1 / Cross-cutting rules",
        "chunk_text": "body of the chunk",
        "score": 0.4231,
        "tenant_id": "demo",
    }


def test_chunk_constructs_with_all_fields() -> None:
    chunk = Chunk(**_full_chunk_kwargs())  # type: ignore[arg-type]
    assert chunk.id == "abc123"
    assert chunk.source == "fca-ps22-9"
    assert chunk.section_path == "Chapter 1 / Cross-cutting rules"
    assert chunk.chunk_text == "body of the chunk"
    assert chunk.score == 0.4231
    assert chunk.tenant_id == "demo"


@pytest.mark.parametrize(
    "missing", ["id", "source", "section_path", "chunk_text", "score", "tenant_id"]
)
def test_chunk_requires_all_fields(missing: str) -> None:
    kwargs = _full_chunk_kwargs()
    del kwargs[missing]
    with pytest.raises(ValidationError):
        Chunk(**kwargs)  # type: ignore[arg-type]


def test_chunk_score_is_float_and_not_str_coercible() -> None:
    """``score`` is StrictFloat: a numeric float is accepted but a string
    representation of a float is rejected. Anchors the contract that a
    malformed search response can't silently land a string score."""
    Chunk(**_full_chunk_kwargs())  # type: ignore[arg-type]  # baseline (float) passes

    bad = _full_chunk_kwargs() | {"score": "0.42"}
    with pytest.raises(ValidationError):
        Chunk(**bad)  # type: ignore[arg-type]
