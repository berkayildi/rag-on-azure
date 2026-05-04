"""Cross-cutting Pydantic models used across the rag_on_azure package.

`Message` is the LLM chat I/O type consumed by the LLMClient adapter.
`Chunk` is the *retrieval-side* search-result type returned by
TenantAwareSearchClient.hybrid_search — distinct from the
*producer-side* `Chunk` defined in ``ingest/src/ingest/chunk.py``,
which has no ``score`` field because it lives upstream of any
retrieval ranking. Keeping them separate keeps the boundary roles
explicit and avoids accidental coupling between ingest and the
serving path.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, StrictFloat


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class Chunk(BaseModel):
    """Retrieval-side chunk; the producer-side counterpart in ingest/chunk.py has no `score` (no ranking has happened at ingest time)."""

    id: str
    source: str
    section_path: str
    chunk_text: str
    # StrictFloat rejects str → float coercion so a malformed Azure
    # Search response can't silently land a string in this field.
    score: StrictFloat
    tenant_id: str
