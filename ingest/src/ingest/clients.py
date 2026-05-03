# Provisional. Day 4 replaces this with `from rag_on_azure.clients.llm import AzureOpenAIClient`. Do not extend the surface beyond embed().
"""Provisional Azure OpenAI embedding client used during Day-3 ingest.

Disposable: on Day 4 ``ingest.index`` switches to the canonical ``LLMClient``
implementation in ``app/`` (see ``docs/design/rag-on-azure.md`` §3.3). Keep
the surface minimal — only ``embed(texts)`` is consumed downstream.

Auth is managed identity end-to-end via ``DefaultAzureCredential`` and the
``cognitiveservices.azure.com`` token scope; no API keys are accepted by
construction.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncAzureOpenAI,
    RateLimitError,
)
from openai.types import CreateEmbeddingResponse
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)

EMBEDDING_BATCH_SIZE = 16
TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"
API_VERSION = "2024-10-21"
MAX_ATTEMPTS = 3

# Exposed module-level so tests can monkeypatch without intercepting tenacity internals.
_RETRY_WAIT = wait_exponential(multiplier=1, min=1, max=8)
_RETRY_STOP = stop_after_attempt(MAX_ATTEMPTS)
_RETRY_TYPES: tuple[type[Exception], ...] = (
    RateLimitError,
    APIConnectionError,
    APITimeoutError,
)


def _batched(seq: list[str], n: int) -> Iterator[list[str]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


class AzureOpenAIEmbeddingClient:
    """Embed batches of texts via Azure OpenAI ``text-embedding-3-small``.

    Construction injects the inner ``AsyncAzureOpenAI`` (tests pass a fake);
    in production the inner client is built from the supplied endpoint,
    deployment name, and a ``DefaultAzureCredential`` + bearer-token provider.
    """

    def __init__(
        self,
        *,
        deployment: str,
        endpoint: str | None = None,
        credential: AsyncTokenCredential | None = None,
        inner: AsyncAzureOpenAI | None = None,
    ) -> None:
        if inner is None:
            if endpoint is None:
                raise ValueError("endpoint is required when inner is not supplied")
            self._credential: AsyncTokenCredential | None = (
                credential or DefaultAzureCredential()
            )
            token_provider = get_bearer_token_provider(self._credential, TOKEN_SCOPE)
            inner = AsyncAzureOpenAI(
                azure_endpoint=endpoint,
                azure_ad_token_provider=token_provider,
                api_version=API_VERSION,
            )
        else:
            self._credential = None  # caller owns the credential

        self._inner = inner
        self._deployment = deployment

    async def embed(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for batch in _batched(texts, EMBEDDING_BATCH_SIZE):
            response = await self._embed_batch(batch)
            results.extend([item.embedding for item in response.data])
        return results

    async def _embed_batch(self, batch: list[str]) -> CreateEmbeddingResponse:
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(_RETRY_TYPES),
            stop=_RETRY_STOP,
            wait=_RETRY_WAIT,
            reraise=True,
        ):
            with attempt:
                response: CreateEmbeddingResponse = await self._inner.embeddings.create(
                    model=self._deployment, input=batch
                )
                return response
        raise RuntimeError(  # pragma: no cover
            "unreachable: AsyncRetrying exited without success or raise"
        )

    async def close(self) -> None:
        """Release the inner OpenAI client and any internally-built credential."""
        await self._inner.close()
        if self._credential is not None:
            await self._credential.close()

    async def __aenter__(self) -> AzureOpenAIEmbeddingClient:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()
