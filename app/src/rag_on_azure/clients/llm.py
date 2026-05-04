"""LLMClient Protocol + AzureOpenAIClient + OpenAIDirectClient stub.

See ``docs/design/rag-on-azure.md`` §3.3.

Cross-package contract: ``ingest/index.py`` imports ``AzureOpenAIClient``
directly and calls ``.embed()`` (no Protocol indirection); ``app/`` graph
nodes consume the ``LLMClient`` Protocol so a fake can substitute.
Single class, two callsites — see Day 4 design D19.

Embedding throttling — see Day 4 design D13 + the wall-clock evidence
captured in the Day 4 Phase A3 ingest run. Sweden Central
``DataZoneStandard`` for ``text-embedding-3-small`` enforces 30,000 TPM
hard. We pre-pace at 24,000 TPM (20% headroom for shared-bucket
contention from other tenants on the subscription), with tenacity-on-
``RateLimitError`` as defence in depth for transient bursts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterator
from typing import Any, Protocol

from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncAzureOpenAI,
    RateLimitError,
)
from openai.types import CreateEmbeddingResponse
from pydantic import BaseModel
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from rag_on_azure.models import Message

log = logging.getLogger(__name__)

# Throttling — see Day 4 D13 and the Phase A3 wall-clock evidence.
EMBEDDING_TPM = 24_000
TOKENS_PER_CHUNK_ESTIMATE = 850
EMBEDDING_BATCH_SIZE = 16

TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"
API_VERSION = "2024-10-21"
MAX_ATTEMPTS = 3

# Module-level so tests can monkeypatch to wait_none() and avoid real backoff.
_RETRY_WAIT = wait_exponential(multiplier=1, min=1, max=8)
_RETRY_STOP = stop_after_attempt(MAX_ATTEMPTS)
_RETRY_TYPES: tuple[type[Exception], ...] = (
    RateLimitError,
    APIConnectionError,
    APITimeoutError,
)


async def _sleep_between_batches(seconds: float) -> None:
    """Throttle indirection so tests can monkeypatch the pacing without
    touching ``asyncio.sleep`` globally (which would break pytest-asyncio
    internals)."""
    await asyncio.sleep(seconds)


class LLMClient(Protocol):
    """Structural Protocol for chat + embedding I/O.

    Implemented by ``AzureOpenAIClient`` (production, managed-identity)
    and ``OpenAIDirectClient`` (stub today, see design spec §0).
    """

    async def complete(
        self, messages: list[Message], schema: type[BaseModel] | None = None
    ) -> Any: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def _batched(seq: list[str], n: int) -> Iterator[list[str]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


class AzureOpenAIClient:
    """LLMClient implementation backed by Azure OpenAI.

    Construction injects the inner ``AsyncAzureOpenAI`` so tests can
    pass a fake; in production it is built from ``endpoint`` plus a
    ``DefaultAzureCredential`` and bearer-token provider — no API key
    is accepted by construction (matches the security-model invariant
    of "no keys in the deployed stack").

    ``chat_deployment`` is optional because today's only consumer
    (``ingest/index.py``) calls ``.embed()`` only. Calling
    ``.complete()`` without it raises a clear ``RuntimeError`` rather
    than a cryptic SDK error.
    """

    def __init__(
        self,
        *,
        embedding_deployment: str,
        chat_deployment: str | None = None,
        endpoint: str | None = None,
        credential: AsyncTokenCredential | None = None,
        inner: AsyncAzureOpenAI | None = None,
        embedding_tpm: int = EMBEDDING_TPM,
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
        self._embedding_deployment = embedding_deployment
        self._chat_deployment = chat_deployment
        self._embedding_tpm = embedding_tpm
        self._closed = False

    @property
    def _batch_sleep_seconds(self) -> float:
        """Pre-batch sleep that keeps us under the TPM ceiling.

        Default constants give 16 chunks × 850 tokens / 24,000 TPM × 60 s
        = 34 s between batches. A 50-batch run pre-paces at ~28 minutes
        with zero 429s instead of letting the SDK absorb 60 s
        ``retry-after`` waits per ceiling hit (the ~70-min pattern we
        saw on Day 4 Phase A3).
        """
        return (
            EMBEDDING_BATCH_SIZE * TOKENS_PER_CHUNK_ESTIMATE / self._embedding_tpm * 60
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        results: list[list[float]] = []
        sleep_s = self._batch_sleep_seconds
        wall_start = time.monotonic()

        batches = list(_batched(texts, EMBEDDING_BATCH_SIZE))
        for i, batch in enumerate(batches):
            if i > 0:
                await _sleep_between_batches(sleep_s)
            response = await self._embed_batch(batch)
            results.extend(item.embedding for item in response.data)

        log.info(
            "embed: %d texts, %d batches, %.1fs wall",
            len(texts),
            len(batches),
            time.monotonic() - wall_start,
        )
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
                    model=self._embedding_deployment, input=batch
                )
                return response
        raise RuntimeError(  # pragma: no cover
            "unreachable: AsyncRetrying exited without success or raise"
        )

    async def complete(
        self, messages: list[Message], schema: type[BaseModel] | None = None
    ) -> Any:
        if self._chat_deployment is None:
            raise RuntimeError(
                "chat_deployment was not set at construction; complete() unavailable"
            )

        api_messages: list[dict[str, str]] = [
            {"role": m.role, "content": m.content} for m in messages
        ]

        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(_RETRY_TYPES),
            stop=_RETRY_STOP,
            wait=_RETRY_WAIT,
            reraise=True,
        ):
            with attempt:
                if schema is not None:
                    completion = await self._inner.chat.completions.parse(
                        model=self._chat_deployment,
                        messages=api_messages,  # type: ignore[arg-type]
                        response_format=schema,
                    )
                    return completion.choices[0].message.parsed
                completion_text = await self._inner.chat.completions.create(
                    model=self._chat_deployment,
                    messages=api_messages,  # type: ignore[arg-type]
                )
                return completion_text.choices[0].message.content
        raise RuntimeError(  # pragma: no cover
            "unreachable: AsyncRetrying exited without success or raise"
        )

    async def close(self) -> None:
        """Idempotent — safe to call twice."""
        if self._closed:
            return
        self._closed = True
        await self._inner.close()
        if self._credential is not None:
            await self._credential.close()

    async def __aenter__(self) -> AzureOpenAIClient:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()


class OpenAIDirectClient:
    """Stub for the OpenAI direct fallback path (design spec §0).

    The deployed stack bans API keys end-to-end (security model);
    OpenAI direct exists as a documented escape hatch only — for local
    dev when Azure OpenAI quota is delayed. Implementation deferred.

    Declares the same ``LLMClient`` Protocol shape as
    ``AzureOpenAIClient`` so mypy treats this as a polymorphic
    substitute and catches misuse at type-check time. Each method
    raises ``NotImplementedError`` with a pointer to the design spec.
    """

    async def complete(
        self, messages: list[Message], schema: type[BaseModel] | None = None
    ) -> Any:
        raise NotImplementedError(
            "OpenAIDirectClient is a stub — see design spec §0 "
            "(docs/design/rag-on-azure.md, fallback narrative). "
            "Use AzureOpenAIClient instead."
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError(
            "OpenAIDirectClient is a stub — see design spec §0 "
            "(docs/design/rag-on-azure.md, fallback narrative). "
            "Use AzureOpenAIClient instead."
        )
