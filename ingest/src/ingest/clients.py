# Provisional. Day 4 replaces this with `from rag_on_azure.clients.llm import AzureOpenAIClient`. Do not extend the surface beyond embed().
"""Provisional Azure OpenAI embedding client used during Day-3 ingest.

Disposable: on Day 4 ``ingest.index`` switches to the canonical ``LLMClient``
implementation in ``app/`` (see ``docs/design/rag-on-azure.md`` §3.3).

Implemented in Phase E of Day 3.
"""

from __future__ import annotations


class AzureOpenAIEmbeddingClient:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError(
            "AzureOpenAIEmbeddingClient.embed is not yet implemented (Phase E)"
        )
