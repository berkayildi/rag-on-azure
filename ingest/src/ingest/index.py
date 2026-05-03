"""Build / refresh the corpus index in Azure AI Search.

Pipeline:

1. Ensure the index exists via ``create_or_update_index`` (idempotent).
2. Read ``ingest/.cache/chunks.jsonl`` (Phase D output).
3. Compute ``content_hash`` for each chunk.
4. Pull existing ``(id, content_hash)`` pairs from the index in one paged sweep.
5. Embed only chunks whose hash is missing or stale.
6. Upload changed chunks in batches.

See ``docs/design/rag-on-azure.md`` §4.4. The ``content_hash`` field captures
anything that affects retrieval semantics — re-tenanting or restructuring
section paths invalidates the cached doc — not just the text body.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Protocol

from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient

from ingest.chunk import CHUNKS_FILENAME, Chunk
from ingest.clients import AzureOpenAIEmbeddingClient
from ingest.schema import INDEX_NAME, create_or_update_index

log = logging.getLogger(__name__)

UPLOAD_BATCH_SIZE = 100
# Azure caps a single search response at top=1000. The reference corpus
# is well under that. A larger corpus would need explicit pagination via
# ``skip=`` or continuation tokens — out of scope for v1.
EXISTING_HASHES_PAGE_SIZE = 1000


class _Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def content_hash(chunk: Chunk) -> str:
    """SHA-256 over (tenant_id, source, section_path, chunk_text).

    Captures anything that affects retrieval semantics, not just the text body —
    re-tenanting a chunk or restructuring its section path invalidates the
    cached document and forces a re-embed and re-upload.
    """
    payload = "".join(
        (chunk.tenant_id, chunk.source, chunk.section_path, chunk.chunk_text)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_chunks(cache_dir: Path) -> list[Chunk]:
    path = cache_dir / CHUNKS_FILENAME
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `ingest chunk` before `ingest index`"
        )
    chunks: list[Chunk] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            chunks.append(Chunk.model_validate_json(line))
    return chunks


async def _existing_hashes(search_client: SearchClient) -> dict[str, str]:
    """Fetch ``id -> content_hash`` for every doc in the index in one sweep.

    Uses ``top=1000``, Azure's per-page cap. The reference corpus is well
    below that; a larger corpus would extend this with continuation tokens.
    """
    out: dict[str, str] = {}
    response = await search_client.search(
        search_text="*",
        select=["id", "content_hash"],
        top=EXISTING_HASHES_PAGE_SIZE,
    )
    async for doc in response:
        out[doc["id"]] = doc.get("content_hash") or ""
    return out


def _to_search_doc(
    chunk: Chunk, embedding: list[float], doc_hash: str
) -> dict[str, Any]:
    return {
        "id": chunk.id,
        "tenant_id": chunk.tenant_id,
        "source": chunk.source,
        "section_path": chunk.section_path,
        "chunk_text": chunk.chunk_text,
        "chunk_vector": embedding,
        "content_hash": doc_hash,
    }


async def index_all(
    cache_dir: Path,
    *,
    embedder: _Embedder,
    search_client: SearchClient,
    search_index_client: SearchIndexClient,
) -> dict[str, int]:
    """Bring the search index up to date with chunks.jsonl.

    Returns a count summary: ``{"unchanged": n, "uploaded": n}``.
    """
    await create_or_update_index(search_index_client)

    chunks = _read_chunks(cache_dir)
    existing = await _existing_hashes(search_client)

    pending: list[tuple[Chunk, str]] = []
    unchanged = 0
    for chunk in chunks:
        h = content_hash(chunk)
        if existing.get(chunk.id) == h:
            unchanged += 1
        else:
            pending.append((chunk, h))

    if not pending:
        log.info("index up to date (%d unchanged)", unchanged)
        return {"unchanged": unchanged, "uploaded": 0}

    log.info("embedding %d chunks (%d unchanged)", len(pending), unchanged)
    embeddings = await embedder.embed([c.chunk_text for c, _ in pending])

    docs = [
        _to_search_doc(chunk, emb, h)
        for (chunk, h), emb in zip(pending, embeddings, strict=True)
    ]

    uploaded = 0
    for i in range(0, len(docs), UPLOAD_BATCH_SIZE):
        batch = docs[i : i + UPLOAD_BATCH_SIZE]
        await search_client.upload_documents(documents=batch)
        uploaded += len(batch)
        log.info("uploaded %d/%d", uploaded, len(docs))

    return {"unchanged": unchanged, "uploaded": uploaded}


_INGEST_DIR = Path(__file__).resolve().parent.parent.parent


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} is required (set via `azd env get-values` or shell export)"
        )
    return value


async def _async_run() -> None:
    search_endpoint = _required_env("AZURE_SEARCH_ENDPOINT")
    openai_endpoint = _required_env("AZURE_OPENAI_ENDPOINT")
    embedding_deployment = _required_env("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
    index_name = os.environ.get("AZURE_SEARCH_INDEX_NAME", INDEX_NAME)

    credential = DefaultAzureCredential()
    embedder = AzureOpenAIEmbeddingClient(
        endpoint=openai_endpoint,
        deployment=embedding_deployment,
        credential=credential,
    )
    search_index_client = SearchIndexClient(
        endpoint=search_endpoint, credential=credential
    )
    search_client = SearchClient(
        endpoint=search_endpoint, index_name=index_name, credential=credential
    )

    try:
        cache_dir = _INGEST_DIR / ".cache"
        result = await index_all(
            cache_dir,
            embedder=embedder,
            search_client=search_client,
            search_index_client=search_index_client,
        )
        log.info("index complete: %s", result)
    finally:
        await embedder.close()
        await search_client.close()
        await search_index_client.close()
        await credential.close()


def run() -> None:
    asyncio.run(_async_run())
