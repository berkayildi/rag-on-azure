"""Unit tests for TenantAwareSearchClient.

Includes the two AUDIT-GRADE tests called out in design spec §5.3 —
``test_missing_tenant_id_raises`` and ``test_cross_tenant_leak_prevented``.
These two tests are the demo's whole credibility; if either regresses,
the multi-tenant story is broken regardless of what other tests pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from rag_on_azure.clients.search import (
    DEFAULT_INDEX_NAME,
    DEFAULT_TOP_K,
    TenantAwareSearchClient,
    _build_filter,
    _escape_odata,
    _validate_tenant_id,
)
from rag_on_azure.models import Chunk

from .conftest import FakeSearchClient


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


@dataclass
class _StubChunk:
    """Minimal _UploadableChunk-shaped object for upload_chunks tests."""

    id: str
    tenant_id: str
    source: str = "fca-test"
    section_path: str = ""
    chunk_text: str = "stub body"


def _doc(
    *,
    id: str,
    tenant_id: str,
    source: str = "fca-test",
    section_path: str = "",
    chunk_text: str = "body",
    score: float = 1.0,
) -> dict[str, Any]:
    return {
        "id": id,
        "tenant_id": tenant_id,
        "source": source,
        "section_path": section_path,
        "chunk_text": chunk_text,
        "@search.score": score,
    }


# ---------------------------------------------------------------------------
# AUDIT-GRADE — design spec §5.3
# ---------------------------------------------------------------------------


def test_missing_tenant_id_raises() -> None:
    """Calling hybrid_search without tenant_id is a TypeError at runtime
    AND a mypy --strict error at type-check time.

    The runtime check is here; the type-check is enforced by CI's
    mypy --strict pass over app/src/. Together they make tenant
    omission impossible by construction — the demo's whole credibility.
    """
    fake = FakeSearchClient()
    client = TenantAwareSearchClient(inner=fake)  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        # The call itself raises before any coroutine is created — Python
        # validates positional args at call time, before the function body
        # runs. We deliberately don't await.
        client.hybrid_search(  # type: ignore[call-arg]
            query="anything",
            query_vector=[0.1] * 1536,
        )


async def test_cross_tenant_leak_prevented() -> None:
    """Tenant A indexes a unique sentinel; tenant B's results NEVER
    contain a tenant-A document — even though tenant A's doc is the
    only one in the index that semantically matches the query.

    Real Azure Search would still return tenant B's own docs ranked by
    relevance (low, since they don't match the query) — that's fine.
    The leak claim is specifically that no tenant-A doc ever crosses
    the boundary, regardless of how relevant its content. That's what
    this test proves.

    Together with test_missing_tenant_id_raises, this is the demo's
    proof that the multi-tenant pattern works.
    """
    sentinel = "ZX9-unique-sentinel-string-tenantA-only-Q7"

    fake = FakeSearchClient(
        docs=[
            _doc(
                id="a-1",
                tenant_id="tenant-a",
                chunk_text=f"intro text {sentinel} more text",
            ),
            _doc(
                id="b-1",
                tenant_id="tenant-b",
                chunk_text="entirely unrelated tenant-b content",
            ),
        ]
    )
    client = TenantAwareSearchClient(inner=fake)  # type: ignore[arg-type]

    # Tenant B searches for tenant A's sentinel string.
    results = await client.hybrid_search(
        query=sentinel,
        query_vector=[0.1] * 1536,
        tenant_id="tenant-b",
    )

    # The leak proof — NO tenant-A doc in results, regardless of
    # how relevant its content is to the query.
    result_ids = {r.id for r in results}
    assert "a-1" not in result_ids, "tenant-A doc leaked into tenant-B results"

    # Defence in depth: every (filter-honouring) result is tenant-B.
    # Catches a future regression that silently widens the filter.
    assert all(r.tenant_id == "tenant-b" for r in results)

    # Confirm the filter actually carried tenant_id eq 'tenant-b' — proves
    # the protection lives at the wrapper boundary, not by accident.
    [search_call] = fake.search_calls
    assert search_call["filter"] == "tenant_id eq 'tenant-b'"

    # Sanity: tenant A *can* see its own sentinel (proves the seed
    # docs weren't just empty and the filter is genuinely the gate).
    results_a = await client.hybrid_search(
        query=sentinel,
        query_vector=[0.1] * 1536,
        tenant_id="tenant-a",
    )
    assert any(r.id == "a-1" for r in results_a)
    assert all(r.tenant_id == "tenant-a" for r in results_a)


# ---------------------------------------------------------------------------
# Filter composition
# ---------------------------------------------------------------------------


def test_escape_odata_doubles_single_quotes() -> None:
    assert _escape_odata("o'reilly") == "o''reilly"
    assert _escape_odata("''") == "''''"
    assert _escape_odata("no apostrophes here") == "no apostrophes here"


def test_build_filter_tenant_only() -> None:
    assert _build_filter("demo-a", None) == "tenant_id eq 'demo-a'"
    assert _build_filter("demo", {}) == "tenant_id eq 'demo'"


def test_build_filter_tenant_first_then_user() -> None:
    """Tenant clause is always the first AND-term."""
    out = _build_filter("demo", {"source": "fca-ps22-9"})
    assert out == "tenant_id eq 'demo' and source eq 'fca-ps22-9'"


def test_build_filter_rejects_invalid_filter_key() -> None:
    """Filter keys flow into the OData string unquoted as field names —
    enforce alphanumeric + underscore."""
    with pytest.raises(ValueError, match="filter key"):
        _build_filter("demo", {"source eq 'x' or 1": "y"})


def test_build_filter_rejects_control_char_filter_value() -> None:
    """A null byte or newline in a filter value would distort log lines
    and could confuse downstream parsers; refuse early."""
    with pytest.raises(ValueError, match="control characters"):
        _build_filter("demo", {"source": "value\x00with-null"})
    with pytest.raises(ValueError, match="control characters"):
        _build_filter("demo", {"source": "value\nwith-newline"})


@pytest.mark.parametrize(
    "tenant_id",
    [
        "demo'; DROP TABLE",
        "demo OR '1'='1",
        "demo\x00",
        "demo\nadmin",
        "DEMO",  # uppercase rejected
        "demo_a",  # underscore rejected
        "demo space",
        "",
        "demo.a",  # dot rejected
    ],
)
def test_validate_tenant_id_rejects_unsafe(tenant_id: str) -> None:
    with pytest.raises(ValueError, match="tenant_id"):
        _validate_tenant_id(tenant_id)


@pytest.mark.parametrize("tenant_id", ["demo", "demo-a", "demo-b", "tenant-1234-x"])
def test_validate_tenant_id_accepts_lowercase_alnum_hyphen(tenant_id: str) -> None:
    _validate_tenant_id(tenant_id)  # no raise


# ---------------------------------------------------------------------------
# hybrid_search behaviour
# ---------------------------------------------------------------------------


async def test_hybrid_search_passes_both_text_and_vector_query() -> None:
    fake = FakeSearchClient(docs=[_doc(id="x", tenant_id="demo")])
    client = TenantAwareSearchClient(inner=fake)  # type: ignore[arg-type]

    await client.hybrid_search(
        query="hello world",
        query_vector=[0.1] * 1536,
        tenant_id="demo",
    )

    [call] = fake.search_calls
    assert call["search_text"] == "hello world"
    assert call["vector_queries"] is not None
    assert len(call["vector_queries"]) == 1
    assert call["vector_queries"][0].vector == [0.1] * 1536


async def test_hybrid_search_top_k_default_is_5() -> None:
    fake = FakeSearchClient()
    client = TenantAwareSearchClient(inner=fake)  # type: ignore[arg-type]

    await client.hybrid_search(query="q", query_vector=[0.0], tenant_id="demo")

    [call] = fake.search_calls
    assert call["top"] == DEFAULT_TOP_K == 5
    assert call["vector_queries"][0].k_nearest_neighbors == 5


async def test_hybrid_search_returns_chunks_with_score() -> None:
    fake = FakeSearchClient(
        docs=[
            _doc(id="d1", tenant_id="demo", chunk_text="first body", score=0.91),
            _doc(id="d2", tenant_id="demo", chunk_text="second body", score=0.42),
        ]
    )
    client = TenantAwareSearchClient(inner=fake)  # type: ignore[arg-type]

    chunks = await client.hybrid_search(
        query="anything",
        query_vector=[0.1] * 1536,
        tenant_id="demo",
    )

    assert len(chunks) == 2
    assert all(isinstance(c, Chunk) for c in chunks)
    by_id = {c.id: c for c in chunks}
    assert by_id["d1"].score == 0.91
    assert by_id["d1"].tenant_id == "demo"
    assert by_id["d1"].chunk_text == "first body"


async def test_hybrid_search_combines_user_filter_with_tenant() -> None:
    fake = FakeSearchClient(
        docs=[
            _doc(id="ps", tenant_id="demo", source="fca-ps22-9"),
            _doc(id="fg", tenant_id="demo", source="fca-fg22-5"),
        ]
    )
    client = TenantAwareSearchClient(inner=fake)  # type: ignore[arg-type]

    chunks = await client.hybrid_search(
        query="x",
        query_vector=[0.1] * 1536,
        tenant_id="demo",
        filters={"source": "fca-ps22-9"},
    )

    assert [c.id for c in chunks] == ["ps"]
    [call] = fake.search_calls
    assert call["filter"] == "tenant_id eq 'demo' and source eq 'fca-ps22-9'"


# ---------------------------------------------------------------------------
# upload_chunks — D26 validated write surface
# ---------------------------------------------------------------------------


async def test_upload_chunks_uploads_with_vector_and_hash() -> None:
    fake = FakeSearchClient()
    client = TenantAwareSearchClient(inner=fake)  # type: ignore[arg-type]

    chunks = [
        _StubChunk(id="c1", tenant_id="demo"),
        _StubChunk(id="c2", tenant_id="demo"),
    ]
    embeddings = [[0.1] * 1536, [0.2] * 1536]
    hashes = ["h1", "h2"]

    await client.upload_chunks(
        chunks=chunks, embeddings=embeddings, content_hashes=hashes
    )

    [batch] = fake.upload_calls
    assert len(batch) == 2
    assert batch[0]["id"] == "c1"
    assert batch[0]["chunk_vector"] == [0.1] * 1536
    assert batch[0]["content_hash"] == "h1"
    assert batch[1]["id"] == "c2"


async def test_upload_chunks_validates_tenant_id_non_empty() -> None:
    """A chunk with empty tenant_id must be refused — the demo's most
    dangerous mistake mode is uploading a doc that no filter can find."""
    fake = FakeSearchClient()
    client = TenantAwareSearchClient(inner=fake)  # type: ignore[arg-type]

    bad = [_StubChunk(id="x", tenant_id="")]
    with pytest.raises(ValueError, match="tenant_id"):
        await client.upload_chunks(chunks=bad, embeddings=[[0.0]], content_hashes=["h"])
    assert fake.upload_calls == []  # nothing was uploaded


async def test_upload_chunks_validates_tenant_id_format() -> None:
    fake = FakeSearchClient()
    client = TenantAwareSearchClient(inner=fake)  # type: ignore[arg-type]

    bad = [_StubChunk(id="x", tenant_id="DEMO_A")]  # uppercase + underscore
    with pytest.raises(ValueError, match="tenant_id"):
        await client.upload_chunks(chunks=bad, embeddings=[[0.0]], content_hashes=["h"])
    assert fake.upload_calls == []


async def test_upload_chunks_rejects_length_mismatch() -> None:
    fake = FakeSearchClient()
    client = TenantAwareSearchClient(inner=fake)  # type: ignore[arg-type]

    chunks = [_StubChunk(id="x", tenant_id="demo")]
    with pytest.raises(ValueError, match="length mismatch"):
        await client.upload_chunks(
            chunks=chunks,
            embeddings=[[0.0], [0.0]],  # 2 embeddings, 1 chunk
            content_hashes=["h"],
        )
    assert fake.upload_calls == []


async def test_upload_chunks_empty_input_is_no_op() -> None:
    fake = FakeSearchClient()
    client = TenantAwareSearchClient(inner=fake)  # type: ignore[arg-type]

    await client.upload_chunks(chunks=[], embeddings=[], content_hashes=[])
    assert fake.upload_calls == []


# ---------------------------------------------------------------------------
# list_existing_hashes — D27 admin escape hatch
# ---------------------------------------------------------------------------


async def test_list_existing_hashes_bypasses_tenant_filter() -> None:
    """Spans all tenants — the documented admin behaviour for the
    ingest idempotence layer. Must NOT pass any tenant filter."""
    fake = FakeSearchClient(
        docs=[
            {"id": "a", "tenant_id": "tenant-a", "content_hash": "h-a"},
            {"id": "b", "tenant_id": "tenant-b", "content_hash": "h-b"},
        ]
    )
    client = TenantAwareSearchClient(inner=fake)  # type: ignore[arg-type]

    hashes = await client.list_existing_hashes()
    assert hashes == {"a": "h-a", "b": "h-b"}

    [call] = fake.search_calls
    assert call["filter"] is None  # explicitly NO tenant filter
    assert call["select"] == ["id", "content_hash"]


# ---------------------------------------------------------------------------
# Lifecycle / construction
# ---------------------------------------------------------------------------


async def test_close_closes_inner() -> None:
    fake = FakeSearchClient()
    client = TenantAwareSearchClient(inner=fake)  # type: ignore[arg-type]
    await client.close()
    assert fake.closed is True
    assert fake.close_calls == 1


async def test_close_idempotent() -> None:
    fake = FakeSearchClient()
    client = TenantAwareSearchClient(inner=fake)  # type: ignore[arg-type]
    await client.close()
    await client.close()
    assert fake.close_calls == 1


async def test_async_context_manager_closes_on_exit() -> None:
    fake = FakeSearchClient()
    async with TenantAwareSearchClient(inner=fake):  # type: ignore[arg-type]
        pass
    assert fake.closed is True


def test_endpoint_required_when_inner_not_supplied() -> None:
    with pytest.raises(ValueError, match="endpoint"):
        TenantAwareSearchClient()


def test_default_index_name_is_corpus() -> None:
    assert DEFAULT_INDEX_NAME == "corpus"
