"""Unit tests for the corpus index schema definition."""

from __future__ import annotations

from typing import Any

from azure.search.documents.indexes.models import LexicalAnalyzerName, SearchIndex

from ingest import schema
from ingest.schema import (
    HNSW_ALGORITHM_NAME,
    HNSW_PROFILE_NAME,
    INDEX_NAME,
    VECTOR_DIMENSIONS,
    build_index,
    create_or_update_index,
)


# ---------------------------------------------------------------------------
# Pure schema shape
# ---------------------------------------------------------------------------


def test_index_name_matches_design_spec() -> None:
    assert build_index().name == INDEX_NAME == "corpus"


def test_field_set_matches_design_spec() -> None:
    fields = {f.name for f in build_index().fields}
    assert fields == {
        "id",
        "tenant_id",
        "source",
        "section_path",
        "chunk_text",
        "chunk_vector",
        "content_hash",
    }


def test_id_field_is_filterable_key() -> None:
    fields = {f.name: f for f in build_index().fields}
    assert fields["id"].key is True
    assert fields["id"].filterable is True


def test_tenant_id_is_filterable_and_facetable() -> None:
    fields = {f.name: f for f in build_index().fields}
    tenant = fields["tenant_id"]
    assert tenant.filterable is True
    assert tenant.facetable is True


def test_source_is_filterable() -> None:
    fields = {f.name: f for f in build_index().fields}
    assert fields["source"].filterable is True


def test_chunk_text_is_searchable_with_en_lucene_analyzer() -> None:
    fields = {f.name: f for f in build_index().fields}
    chunk_text = fields["chunk_text"]
    assert chunk_text.searchable is True
    assert chunk_text.analyzer_name == LexicalAnalyzerName.EN_LUCENE


def test_chunk_vector_is_1536_dim_hnsw() -> None:
    fields = {f.name: f for f in build_index().fields}
    vec = fields["chunk_vector"]
    assert vec.searchable is True
    assert vec.vector_search_dimensions == VECTOR_DIMENSIONS == 1536
    assert vec.vector_search_profile_name == HNSW_PROFILE_NAME


def test_content_hash_is_filterable() -> None:
    fields = {f.name: f for f in build_index().fields}
    assert fields["content_hash"].filterable is True


def test_hnsw_parameters_match_design_spec() -> None:
    index = build_index()
    assert index.vector_search is not None
    [algo] = index.vector_search.algorithms
    assert algo.name == HNSW_ALGORITHM_NAME
    assert algo.parameters is not None
    assert algo.parameters.m == 4
    assert algo.parameters.ef_construction == 400
    assert algo.parameters.ef_search == 500


def test_vector_profile_references_algorithm() -> None:
    index = build_index()
    assert index.vector_search is not None
    [profile] = index.vector_search.profiles
    assert profile.name == HNSW_PROFILE_NAME
    assert profile.algorithm_configuration_name == HNSW_ALGORITHM_NAME


# ---------------------------------------------------------------------------
# create_or_update_index orchestration
# ---------------------------------------------------------------------------


class _FakeIndexClient:
    def __init__(self) -> None:
        self.calls: list[SearchIndex] = []

    async def create_or_update_index(self, index: SearchIndex) -> SearchIndex:
        self.calls.append(index)
        return index


async def test_create_or_update_index_passes_built_index() -> None:
    fake = _FakeIndexClient()
    result = await create_or_update_index(fake)  # type: ignore[arg-type]
    assert len(fake.calls) == 1
    assert fake.calls[0].name == INDEX_NAME
    assert result.name == INDEX_NAME


async def test_create_or_update_index_is_idempotent_in_shape() -> None:
    """Two consecutive calls produce two SearchIndex objects with identical shape."""
    fake = _FakeIndexClient()
    await create_or_update_index(fake)  # type: ignore[arg-type]
    await create_or_update_index(fake)  # type: ignore[arg-type]
    assert len(fake.calls) == 2
    a, b = fake.calls

    def _shape(idx: SearchIndex) -> dict[str, Any]:
        return {
            "name": idx.name,
            "fields": sorted(f.name for f in idx.fields),
            "algorithms": sorted(
                a.name
                for a in (idx.vector_search.algorithms if idx.vector_search else [])
            ),
        }

    assert _shape(a) == _shape(b)


def test_module_exposes_build_index_for_external_callers() -> None:
    """Anchor: app/ tooling will import build_index — keep it on the module."""
    assert callable(schema.build_index)
