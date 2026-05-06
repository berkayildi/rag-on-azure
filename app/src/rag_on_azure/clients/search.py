"""TenantAwareSearchClient — the multi-tenant retrieval boundary.

See ``docs/design/rag-on-azure.md`` §3.3 + §5.

Design invariants (audit-grade per §5.3):

- ``hybrid_search()`` requires ``tenant_id`` as a non-optional argument.
  Calling without it is a ``TypeError`` at runtime AND a ``mypy --strict``
  error at type-check time. There is no sibling search method that omits
  the tenant — the wrapper class is the boundary.
- The OData filter is composed inside the client, *not* by the caller.
  Tenant-id is always the first AND-clause; user-supplied filters are
  appended. Single quotes inside any filter value are doubled per the
  OData spec to defeat injection.
- Tenant-id values are validated against ``^[a-z0-9-]+$`` before being
  embedded in the filter string. Defence in depth: even if a JWT
  validator regression let a malformed tenant_id reach the search
  client, this layer would refuse to construct a filter from it.
- Index *writes* go through this wrapper too (``upload_chunks``), with
  per-chunk tenant_id validation. The ``list_existing_hashes`` method
  is the single named admin escape hatch that *deliberately* spans all
  tenants; it's documented as ingest-only.

These invariants are exercised by ``test_cross_tenant_leak_prevented``
and ``test_missing_tenant_id_raises`` in ``tests/unit/test_search.py``
— the two audit-grade tests that ground the demo's whole credibility.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Protocol

from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizedQuery

from rag_on_azure.models import Chunk

log = logging.getLogger(__name__)

DEFAULT_INDEX_NAME = "corpus"
VECTOR_FIELD = "chunk_vector"
DEFAULT_TOP_K = 5
# Azure Search per-page cap; admin sweep uses this. The reference corpus
# is well below it; a larger corpus would extend with continuation tokens.
ADMIN_PAGE_SIZE = 1000

# Validation: tenant_ids in this stack are issuer-controlled identifiers
# (JWT `tenant_id` claim). We restrict to lowercase alphanumeric + hyphen
# — matches the manifest's `demo`, `demo-a`, `demo-b` style. Forks issuing
# tenant_ids in other shapes (uppercase, underscores) will need to relax
# this regex; document in your IdP integration notes.
_TENANT_ID_RE = re.compile(r"^[a-z0-9-]+$")
# User-supplied filter values: reject control characters (null bytes,
# newlines, etc.) that could distort log lines or downstream parsing.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


class _UploadableChunk(Protocol):
    """Structural protocol for what ``upload_chunks`` accepts.

    Avoids a hard cross-package dep on ``ingest.chunk.Chunk`` (which would
    invert the intended app→ingest dependency direction). The retrieval-
    side ``rag_on_azure.models.Chunk`` is *not* used here either — its
    ``score: StrictFloat`` makes no sense pre-retrieval. ingest's existing
    producer-side Chunk satisfies this protocol structurally.
    """

    id: str
    tenant_id: str
    source: str
    section_path: str
    chunk_text: str


def _validate_tenant_id(tenant_id: str) -> None:
    if not tenant_id:
        raise ValueError("tenant_id must be non-empty")
    if not _TENANT_ID_RE.fullmatch(tenant_id):
        raise ValueError(
            f"tenant_id {tenant_id!r} must match ^[a-z0-9-]+$ "
            "(lowercase alphanumeric and hyphen only)"
        )


def _validate_filter_value(value: str, *, role: str) -> None:
    if _CONTROL_CHARS_RE.search(value):
        raise ValueError(f"{role} contains control characters; refusing")


def _escape_odata(s: str) -> str:
    """Per OData spec, a literal single quote inside a string literal is
    written as two single quotes. This is the canonical injection
    defence for OData filter strings.
    """
    return s.replace("'", "''")


def _build_filter(tenant_id: str, user_filters: dict[str, str] | None) -> str:
    """Compose an OData ``$filter`` string with tenant_id always first.

    Caller invariant: ``_validate_tenant_id(tenant_id)`` has been called.
    User filters are validated for control-character safety here; keys
    are validated as field names (alphanumeric + underscore) since they
    flow into the filter unquoted as field references.
    """
    parts: list[str] = [f"tenant_id eq '{_escape_odata(tenant_id)}'"]
    if user_filters:
        for key, value in user_filters.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError(
                    f"filter key {key!r} must be a valid field name "
                    "(alphanumeric + underscore, not starting with a digit)"
                )
            _validate_filter_value(value, role=f"filter value for {key!r}")
            parts.append(f"{key} eq '{_escape_odata(value)}'")
    return " and ".join(parts)


class TenantAwareSearchClient:
    """The multi-tenant retrieval + write boundary for the corpus index.

    Construction injects an optional ``inner`` ``SearchClient`` for tests;
    in production it is built from ``endpoint`` + ``index_name`` +
    ``DefaultAzureCredential`` — no API key path.
    """

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        index_name: str = DEFAULT_INDEX_NAME,
        credential: AsyncTokenCredential | None = None,
        inner: SearchClient | None = None,
    ) -> None:
        if inner is None:
            if endpoint is None:
                raise ValueError("endpoint is required when inner is not supplied")
            self._credential: AsyncTokenCredential | None = (
                credential or DefaultAzureCredential()
            )
            inner = SearchClient(
                endpoint=endpoint,
                index_name=index_name,
                credential=self._credential,
            )
        else:
            self._credential = None  # caller owns the credential

        self._inner = inner
        self._index_name = index_name
        self._closed = False

    async def hybrid_search(
        self,
        query: str,
        query_vector: list[float],
        tenant_id: str,
        filters: dict[str, str] | None = None,
        top_k: int = DEFAULT_TOP_K,
    ) -> list[Chunk]:
        """Hybrid BM25 + vector search, tenant-filtered, RRF-fused by Azure.

        ``query_vector`` is supplied by the caller (the orchestrator owns
        the embedder lifecycle and any query-embedding cache) — see Day 4
        D24. ``tenant_id`` is positional-or-keyword but never optional.
        """
        _validate_tenant_id(tenant_id)
        odata_filter = _build_filter(tenant_id, filters)

        vec_query = VectorizedQuery(
            vector=query_vector,
            k_nearest_neighbors=top_k,
            fields=VECTOR_FIELD,
        )
        response = await self._inner.search(
            search_text=query,
            vector_queries=[vec_query],
            filter=odata_filter,
            select=["id", "tenant_id", "source", "section_path", "chunk_text"],
            top=top_k,
        )

        chunks: list[Chunk] = []
        async for doc in response:
            chunks.append(
                Chunk(
                    id=doc["id"],
                    source=doc["source"],
                    section_path=doc.get("section_path", "") or "",
                    chunk_text=doc["chunk_text"],
                    score=float(doc["@search.score"]),
                    tenant_id=doc["tenant_id"],
                )
            )
        return chunks

    async def upload_chunks(
        self,
        chunks: list[_UploadableChunk],
        embeddings: list[list[float]],
        content_hashes: list[str],
    ) -> None:
        """Validated batch upload — refuses any chunk without a valid
        tenant_id. The single mutation surface for the corpus index.

        Length mismatch between the three lists is also fatal.
        """
        if not (len(chunks) == len(embeddings) == len(content_hashes)):
            raise ValueError(
                f"length mismatch: chunks={len(chunks)}, "
                f"embeddings={len(embeddings)}, content_hashes={len(content_hashes)}"
            )
        if not chunks:
            return

        for chunk in chunks:
            _validate_tenant_id(chunk.tenant_id)

        docs: list[dict[str, Any]] = [
            {
                "id": chunk.id,
                "tenant_id": chunk.tenant_id,
                "source": chunk.source,
                "section_path": chunk.section_path,
                "chunk_text": chunk.chunk_text,
                VECTOR_FIELD: embedding,
                "content_hash": content_hash,
            }
            for chunk, embedding, content_hash in zip(
                chunks, embeddings, content_hashes, strict=True
            )
        ]
        await self._inner.upload_documents(documents=docs)

    async def list_existing_hashes(self) -> dict[str, str]:
        """Admin escape hatch — DELIBERATELY bypasses tenant isolation.

        Returns ``{doc_id: content_hash}`` across all tenants in the
        index. Used by ingest's idempotence layer to identify which
        chunks need re-embedding. Callable from ingest's
        single-process bootstrap only — not exposed via any user-facing
        API surface.
        """
        out: dict[str, str] = {}
        response = await self._inner.search(
            search_text="*",
            select=["id", "content_hash"],
            top=ADMIN_PAGE_SIZE,
        )
        async for doc in response:
            out[doc["id"]] = doc.get("content_hash") or ""
        return out

    async def ping(self) -> None:
        """Cheap readiness probe — credential + index reachable.

        Calls ``get_document_count`` (single REST round-trip, returns an
        ``int``). Deliberately tenant-less: readiness is a connectivity
        question, not a data-access one. The role attached to this
        client (``Search Index Data Reader``) covers it.
        """
        await self._inner.get_document_count()

    async def close(self) -> None:
        """Idempotent — safe to call twice."""
        if self._closed:
            return
        self._closed = True
        await self._inner.close()
        if self._credential is not None:
            await self._credential.close()

    async def __aenter__(self) -> TenantAwareSearchClient:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()
