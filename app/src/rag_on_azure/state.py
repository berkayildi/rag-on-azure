"""Shared LangGraph state passed between the three pipeline nodes.

See ``docs/design/rag-on-azure.md`` §3.1.

Phase B deviation from the spec's illustrative state: a ``top_k`` field
is added so ``RagRequest.top_k`` can flow from the API handler through
the graph into the retrieve node. The alternative (LangGraph's runtime
``config`` parameter) added cognitive load for negligible benefit.
``metadata`` is typed as ``dict[str, Any]`` for ``mypy --strict``;
the spec wrote it untyped.

Invariant: ``tenant_id`` enters the graph from the FastAPI handler,
sourced from the JWT via ``get_current_tenant``. The graph itself is
auth-agnostic — it never reads JWTs and has no path to override the
tenant. This preserves the §5.3 audit-grade boundary.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from rag_on_azure.api.schemas import Citation
from rag_on_azure.models import Chunk


class GraphState(BaseModel):
    question: str
    tenant_id: str
    top_k: int = 5
    rewritten_query: str | None = None
    filters: dict[str, str] = Field(default_factory=dict)
    retrieved_chunks: list[Chunk] = Field(default_factory=list)
    answer: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
