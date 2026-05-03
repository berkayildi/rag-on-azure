"""Unit tests for the index orchestrator."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from ingest.chunk import CHUNKS_FILENAME, Chunk
from ingest.index import UPLOAD_BATCH_SIZE, content_hash, index_all
from ingest.schema import INDEX_NAME


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeEmbedder:
    def __init__(self, *, dim: int = 1536) -> None:
        self.calls: list[list[str]] = []
        self._dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(i) / 1000] * self._dim for i, _ in enumerate(texts)]


class _FakeSearchClient:
    def __init__(self, *, existing: list[dict[str, Any]] | None = None) -> None:
        self._existing = list(existing or [])
        self.uploaded: list[list[dict[str, Any]]] = []
        self.search_calls: list[dict[str, Any]] = []

    async def search(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        self.search_calls.append(kwargs)

        async def _aiter() -> AsyncIterator[dict[str, Any]]:
            for doc in self._existing:
                yield doc

        return _aiter()

    async def upload_documents(self, *, documents: list[dict[str, Any]]) -> None:
        self.uploaded.append(list(documents))


class _FakeSearchIndexClient:
    def __init__(self) -> None:
        self.create_or_update_calls: list[Any] = []

    async def create_or_update_index(self, index: Any) -> Any:
        self.create_or_update_calls.append(index)
        return index


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_chunks(cache: Path, chunks: list[Chunk]) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    (cache / CHUNKS_FILENAME).write_text(
        "\n".join(c.model_dump_json() for c in chunks) + "\n",
        encoding="utf-8",
    )


def _chunk(
    *,
    id: str,
    text: str = "body",
    tenant: str = "demo",
    source: str = "src",
    section: str = "Section",
) -> Chunk:
    return Chunk(
        id=id,
        tenant_id=tenant,
        source=source,
        section_path=section,
        chunk_text=text,
    )


# ---------------------------------------------------------------------------
# content_hash semantics
# ---------------------------------------------------------------------------


def test_content_hash_changes_with_chunk_text() -> None:
    a = _chunk(id="x", text="hello")
    b = _chunk(id="x", text="hello world")
    assert content_hash(a) != content_hash(b)


def test_content_hash_changes_with_tenant_id() -> None:
    a = _chunk(id="x", tenant="demo-a")
    b = _chunk(id="x", tenant="demo-b")
    assert content_hash(a) != content_hash(b)


def test_content_hash_changes_with_section_path() -> None:
    a = _chunk(id="x", section="A / B")
    b = _chunk(id="x", section="A / C")
    assert content_hash(a) != content_hash(b)


def test_content_hash_changes_with_source() -> None:
    a = _chunk(id="x", source="src-1")
    b = _chunk(id="x", source="src-2")
    assert content_hash(a) != content_hash(b)


def test_content_hash_stable_across_runs() -> None:
    a = _chunk(id="x", text="hello")
    assert content_hash(a) == content_hash(a)


# ---------------------------------------------------------------------------
# index_all integration
# ---------------------------------------------------------------------------


async def test_empty_index_uploads_all_chunks(tmp_path: Path) -> None:
    cache = tmp_path / ".cache"
    chunks = [_chunk(id=f"c{i}", text=f"text {i}") for i in range(3)]
    _write_chunks(cache, chunks)

    embedder = _FakeEmbedder()
    sc = _FakeSearchClient()
    sic = _FakeSearchIndexClient()

    result = await index_all(
        cache,
        embedder=embedder,  # type: ignore[arg-type]
        search_client=sc,  # type: ignore[arg-type]
        search_index_client=sic,  # type: ignore[arg-type]
    )

    assert result == {"unchanged": 0, "uploaded": 3}
    assert len(sic.create_or_update_calls) == 1
    assert sic.create_or_update_calls[0].name == INDEX_NAME

    assert len(embedder.calls) == 1
    assert embedder.calls[0] == ["text 0", "text 1", "text 2"]

    assert len(sc.uploaded) == 1
    assert len(sc.uploaded[0]) == 3
    doc = sc.uploaded[0][0]
    assert set(doc) == {
        "id",
        "tenant_id",
        "source",
        "section_path",
        "chunk_text",
        "chunk_vector",
        "content_hash",
    }
    assert len(doc["chunk_vector"]) == 1536


async def test_all_hashes_match_no_embed_no_upload(tmp_path: Path) -> None:
    cache = tmp_path / ".cache"
    chunks = [_chunk(id="c1", text="hello"), _chunk(id="c2", text="world")]
    _write_chunks(cache, chunks)

    existing = [
        {"id": chunks[0].id, "content_hash": content_hash(chunks[0])},
        {"id": chunks[1].id, "content_hash": content_hash(chunks[1])},
    ]
    embedder = _FakeEmbedder()
    sc = _FakeSearchClient(existing=existing)
    sic = _FakeSearchIndexClient()

    result = await index_all(
        cache,
        embedder=embedder,  # type: ignore[arg-type]
        search_client=sc,  # type: ignore[arg-type]
        search_index_client=sic,  # type: ignore[arg-type]
    )

    assert result == {"unchanged": 2, "uploaded": 0}
    assert embedder.calls == []
    assert sc.uploaded == []


async def test_only_stale_and_missing_are_processed(tmp_path: Path) -> None:
    cache = tmp_path / ".cache"
    chunks = [
        _chunk(id="c1", text="fresh"),
        _chunk(id="c2", text="stale"),
        _chunk(id="c3", text="missing"),
    ]
    _write_chunks(cache, chunks)

    existing = [
        {"id": "c1", "content_hash": content_hash(chunks[0])},
        {"id": "c2", "content_hash": "stale-hash-from-old-run"},
    ]
    embedder = _FakeEmbedder()
    sc = _FakeSearchClient(existing=existing)
    sic = _FakeSearchIndexClient()

    result = await index_all(
        cache,
        embedder=embedder,  # type: ignore[arg-type]
        search_client=sc,  # type: ignore[arg-type]
        search_index_client=sic,  # type: ignore[arg-type]
    )

    assert result == {"unchanged": 1, "uploaded": 2}
    assert embedder.calls[0] == ["stale", "missing"]
    [batch] = sc.uploaded
    assert {d["id"] for d in batch} == {"c2", "c3"}


async def test_upload_batches_at_documented_size(tmp_path: Path) -> None:
    cache = tmp_path / ".cache"
    n = UPLOAD_BATCH_SIZE * 2 + 5
    chunks = [_chunk(id=f"c{i}", text=f"t{i}") for i in range(n)]
    _write_chunks(cache, chunks)

    embedder = _FakeEmbedder()
    sc = _FakeSearchClient()
    sic = _FakeSearchIndexClient()

    result = await index_all(
        cache,
        embedder=embedder,  # type: ignore[arg-type]
        search_client=sc,  # type: ignore[arg-type]
        search_index_client=sic,  # type: ignore[arg-type]
    )

    assert result == {"unchanged": 0, "uploaded": n}
    assert [len(batch) for batch in sc.uploaded] == [
        UPLOAD_BATCH_SIZE,
        UPLOAD_BATCH_SIZE,
        5,
    ]


async def test_search_query_selects_only_id_and_content_hash(tmp_path: Path) -> None:
    cache = tmp_path / ".cache"
    _write_chunks(cache, [_chunk(id="c1", text="t1")])

    sc = _FakeSearchClient()
    await index_all(
        cache,
        embedder=_FakeEmbedder(),  # type: ignore[arg-type]
        search_client=sc,  # type: ignore[arg-type]
        search_index_client=_FakeSearchIndexClient(),  # type: ignore[arg-type]
    )

    [call] = sc.search_calls
    assert call.get("select") == ["id", "content_hash"]
    assert call.get("top") == 1000  # cap mentioned in module comment


async def test_create_or_update_called_before_search(tmp_path: Path) -> None:
    """Anchor: index must exist before we query for hashes on first run."""
    cache = tmp_path / ".cache"
    _write_chunks(cache, [_chunk(id="c1", text="t1")])

    order: list[str] = []

    class _OrderedSearchIndexClient(_FakeSearchIndexClient):
        async def create_or_update_index(self, index: Any) -> Any:
            order.append("create_or_update_index")
            return await super().create_or_update_index(index)

    class _OrderedSearchClient(_FakeSearchClient):
        async def search(self, **kwargs: Any) -> Any:
            order.append("search")
            return await super().search(**kwargs)

    await index_all(
        cache,
        embedder=_FakeEmbedder(),  # type: ignore[arg-type]
        search_client=_OrderedSearchClient(),  # type: ignore[arg-type]
        search_index_client=_OrderedSearchIndexClient(),  # type: ignore[arg-type]
    )

    assert order == ["create_or_update_index", "search"]


async def test_missing_chunks_jsonl_raises(tmp_path: Path) -> None:
    cache = tmp_path / ".cache"
    cache.mkdir()

    with pytest.raises(FileNotFoundError, match=CHUNKS_FILENAME):
        await index_all(
            cache,
            embedder=_FakeEmbedder(),  # type: ignore[arg-type]
            search_client=_FakeSearchClient(),  # type: ignore[arg-type]
            search_index_client=_FakeSearchIndexClient(),  # type: ignore[arg-type]
        )
