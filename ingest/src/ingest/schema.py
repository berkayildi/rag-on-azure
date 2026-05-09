"""Definition of the ``corpus`` Azure AI Search index.

The schema lives in Python (not Bicep) because index shape is application data —
its lifecycle ties to the app, not to the cloud resource. See
``docs/design/rag-on-azure.md`` §2.2.

Field set:

- ``id``           — key, filterable
- ``tenant_id``    — filterable, facetable (drives the JWT-driven tenant filter)
- ``source``       — filterable
- ``section_path`` — retrieved with results, not searched
- ``chunk_text``   — searchable, ``en.lucene`` analyzer
- ``chunk_vector`` — 1536-dim, HNSW (m=4, efConstruction=400, efSearch=500)
- ``content_hash`` — filterable; drives idempotent re-indexing in ingest.index

The ``content_hash`` field augments §2.2 of the design spec; it is not
mentioned there but §4.4 implies a hash mechanism for the idempotent
re-ingest path. A doc follow-up should pick this up.
"""

from __future__ import annotations

from azure.search.documents.indexes.aio import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    HnswParameters,
    LexicalAnalyzerName,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)

INDEX_NAME = "corpus"
VECTOR_DIMENSIONS = 1536
HNSW_ALGORITHM_NAME = "corpus-hnsw"
HNSW_PROFILE_NAME = "corpus-hnsw-profile"
HNSW_M = 4
HNSW_EF_CONSTRUCTION = 400
HNSW_EF_SEARCH = 500


def build_index() -> SearchIndex:
    """Construct the ``corpus`` SearchIndex object — pure, no I/O.

    Field ``type=`` values use ``.value`` extraction (e.g.
    ``SearchFieldDataType.String.value``) and an f-string for the vector
    Collection because azure-search-documents 12.x changed
    ``SearchFieldDataType`` from a string-typed class to a
    ``str``-subclassing ``Enum`` driven by ``CaseInsensitiveEnumMeta``.
    Runtime accepts both Enum members and plain strings, but mypy
    --strict cannot see through the metaclass's str inheritance and
    rejects bare Enum members against ``SimpleField(type=str | …)`` and
    rejects ``SearchFieldDataType.Collection(...)`` as not callable.
    Extracting ``.value`` produces the underlying ``Edm.*`` string and
    sidesteps the typing quirk entirely.
    """
    edm_string = SearchFieldDataType.String.value
    edm_vector_collection = f"Collection({SearchFieldDataType.Single.value})"

    fields: list[SearchField] = [
        SimpleField(
            name="id",
            type=edm_string,
            key=True,
            filterable=True,
        ),
        SimpleField(
            name="tenant_id",
            type=edm_string,
            filterable=True,
            facetable=True,
        ),
        SimpleField(
            name="source",
            type=edm_string,
            filterable=True,
        ),
        SimpleField(
            name="section_path",
            type=edm_string,
        ),
        SearchableField(
            name="chunk_text",
            type=edm_string,
            analyzer_name=LexicalAnalyzerName.EN_LUCENE,
        ),
        SearchField(
            name="chunk_vector",
            type=edm_vector_collection,
            searchable=True,
            vector_search_dimensions=VECTOR_DIMENSIONS,
            vector_search_profile_name=HNSW_PROFILE_NAME,
        ),
        SimpleField(
            name="content_hash",
            type=edm_string,
            filterable=True,
        ),
    ]

    vector_search = VectorSearch(
        algorithms=[
            HnswAlgorithmConfiguration(
                name=HNSW_ALGORITHM_NAME,
                parameters=HnswParameters(
                    m=HNSW_M,
                    ef_construction=HNSW_EF_CONSTRUCTION,
                    ef_search=HNSW_EF_SEARCH,
                ),
            )
        ],
        profiles=[
            VectorSearchProfile(
                name=HNSW_PROFILE_NAME,
                algorithm_configuration_name=HNSW_ALGORITHM_NAME,
            )
        ],
    )

    return SearchIndex(
        name=INDEX_NAME,
        fields=fields,
        vector_search=vector_search,
    )


async def create_or_update_index(client: SearchIndexClient) -> SearchIndex:
    """Idempotently create or update the corpus index.

    Re-runs are no-ops when shape is stable; if the schema changes,
    Azure may take the index offline briefly to apply the update.
    """
    index = build_index()
    return await client.create_or_update_index(index)
