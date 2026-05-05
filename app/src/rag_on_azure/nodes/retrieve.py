"""``retrieve`` — embeds the query and runs hybrid BM25 + vector search.

See ``docs/design/rag-on-azure.md`` §3.2 + §5.

The node embeds the (rewritten) query inline because
``TenantAwareSearchClient.hybrid_search`` requires both a text query
and a query vector — that's the search-client signature locked in
during Day 4. One extra LLM round-trip per ``/query`` is the cost of
keeping the embedder lifecycle out of the search client.

Falls back to ``state.question`` if ``rewritten_query`` is empty —
defensive default so a soft failure in ``understand`` doesn't strand
retrieval. ``state.tenant_id`` flows verbatim into the search client,
which enforces the §5.3 audit-grade tenant filter.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from rag_on_azure.clients.llm import LLMClient
from rag_on_azure.clients.search import TenantAwareSearchClient
from rag_on_azure.state import GraphState


def make_retrieve_node(
    llm: LLMClient,
    search: TenantAwareSearchClient,
) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
    async def retrieve(state: GraphState) -> dict[str, Any]:
        query = state.rewritten_query or state.question
        embeddings = await llm.embed([query])
        chunks = await search.hybrid_search(
            query=query,
            query_vector=embeddings[0],
            tenant_id=state.tenant_id,
            filters=state.filters or None,
            top_k=state.top_k,
        )
        return {"retrieved_chunks": chunks}

    return retrieve
