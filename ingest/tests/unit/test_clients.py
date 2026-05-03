"""Unit tests for the provisional Azure OpenAI embedding client."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from tenacity import wait_none

import ingest.clients as clients_mod
from ingest.clients import EMBEDDING_BATCH_SIZE, AzureOpenAIEmbeddingClient


@dataclass
class _FakeEmbedding:
    embedding: list[float]


@dataclass
class _FakeEmbeddingResponse:
    data: list[_FakeEmbedding]


@dataclass
class _FakeCall:
    model: str
    input: list[str]


class _FakeEmbeddingsAPI:
    def __init__(
        self,
        *,
        embedding_dim: int = 1536,
        exceptions: list[Exception] | None = None,
    ) -> None:
        self.calls: list[_FakeCall] = []
        self._dim = embedding_dim
        self._exceptions: list[Exception] = list(exceptions or [])

    async def create(self, *, model: str, input: list[str]) -> _FakeEmbeddingResponse:
        self.calls.append(_FakeCall(model=model, input=list(input)))
        if self._exceptions:
            raise self._exceptions.pop(0)
        return _FakeEmbeddingResponse(
            data=[_FakeEmbedding(embedding=[0.1] * self._dim) for _ in input]
        )


@dataclass
class _FakeAsyncOpenAI:
    embeddings: _FakeEmbeddingsAPI = field(default_factory=_FakeEmbeddingsAPI)
    closed: bool = False

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(clients_mod, "_RETRY_WAIT", wait_none())
    yield


# ---------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------


async def test_batches_inputs_of_sixteen() -> None:
    inner = _FakeAsyncOpenAI()
    client = AzureOpenAIEmbeddingClient(
        deployment="text-embedding-3-small",
        inner=inner,  # type: ignore[arg-type]
    )

    texts = [f"text {i}" for i in range(35)]
    embeddings = await client.embed(texts)

    assert len(embeddings) == 35
    assert [len(c.input) for c in inner.embeddings.calls] == [16, 16, 3]


async def test_batch_size_constant_matches_documented() -> None:
    assert EMBEDDING_BATCH_SIZE == 16


async def test_passes_deployment_name_to_each_call() -> None:
    inner = _FakeAsyncOpenAI()
    client = AzureOpenAIEmbeddingClient(
        deployment="text-embedding-3-small",
        inner=inner,  # type: ignore[arg-type]
    )
    await client.embed(["a", "b"])
    assert inner.embeddings.calls[0].model == "text-embedding-3-small"


async def test_returns_one_embedding_per_input_in_order() -> None:
    inner = _FakeAsyncOpenAI()
    client = AzureOpenAIEmbeddingClient(
        deployment="d",
        inner=inner,  # type: ignore[arg-type]
    )

    embeddings = await client.embed(["a", "b", "c"])
    assert len(embeddings) == 3
    assert all(len(e) == 1536 for e in embeddings)


async def test_empty_input_makes_no_calls() -> None:
    inner = _FakeAsyncOpenAI()
    client = AzureOpenAIEmbeddingClient(
        deployment="d",
        inner=inner,  # type: ignore[arg-type]
    )
    embeddings = await client.embed([])
    assert embeddings == []
    assert inner.embeddings.calls == []


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


class _Transient(Exception):
    pass


async def test_retries_on_transient_error_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clients_mod, "_RETRY_TYPES", (_Transient,))

    inner = _FakeAsyncOpenAI(
        embeddings=_FakeEmbeddingsAPI(exceptions=[_Transient("rate limited")])
    )
    client = AzureOpenAIEmbeddingClient(
        deployment="d",
        inner=inner,  # type: ignore[arg-type]
    )

    embeddings = await client.embed(["a"])
    assert len(embeddings) == 1
    assert len(inner.embeddings.calls) == 2  # one failure + one success


async def test_retries_exhausted_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clients_mod, "_RETRY_TYPES", (_Transient,))

    inner = _FakeAsyncOpenAI(
        embeddings=_FakeEmbeddingsAPI(
            exceptions=[_Transient("a"), _Transient("b"), _Transient("c")]
        )
    )
    client = AzureOpenAIEmbeddingClient(
        deployment="d",
        inner=inner,  # type: ignore[arg-type]
    )

    with pytest.raises(_Transient):
        await client.embed(["a"])
    assert len(inner.embeddings.calls) == 3  # MAX_ATTEMPTS


async def test_non_retryable_exception_propagates_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clients_mod, "_RETRY_TYPES", (_Transient,))

    class _Fatal(Exception):
        pass

    inner = _FakeAsyncOpenAI(embeddings=_FakeEmbeddingsAPI(exceptions=[_Fatal("auth")]))
    client = AzureOpenAIEmbeddingClient(
        deployment="d",
        inner=inner,  # type: ignore[arg-type]
    )

    with pytest.raises(_Fatal):
        await client.embed(["a"])
    assert len(inner.embeddings.calls) == 1  # no retries


# ---------------------------------------------------------------------------
# Lifecycle / construction
# ---------------------------------------------------------------------------


async def test_close_closes_inner_client() -> None:
    inner = _FakeAsyncOpenAI()
    client = AzureOpenAIEmbeddingClient(
        deployment="d",
        inner=inner,  # type: ignore[arg-type]
    )
    await client.close()
    assert inner.closed is True


async def test_async_context_manager_closes_inner() -> None:
    inner = _FakeAsyncOpenAI()
    async with AzureOpenAIEmbeddingClient(
        deployment="d",
        inner=inner,  # type: ignore[arg-type]
    ):
        pass
    assert inner.closed is True


def test_endpoint_required_when_inner_not_supplied() -> None:
    with pytest.raises(ValueError, match="endpoint"):
        AzureOpenAIEmbeddingClient(deployment="d")


def test_constructed_inner_is_attached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Construction with endpoint+deployment builds an AsyncAzureOpenAI instance.

    Patches DefaultAzureCredential and AsyncAzureOpenAI to avoid any real auth.
    """
    sentinel: dict[str, Any] = {}

    class _FakeCred:
        async def close(self) -> None:
            sentinel["cred_closed"] = True

    class _FakeOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            sentinel["openai_kwargs"] = kwargs

        async def close(self) -> None:
            sentinel["openai_closed"] = True

    monkeypatch.setattr(clients_mod, "DefaultAzureCredential", _FakeCred)
    monkeypatch.setattr(clients_mod, "AsyncAzureOpenAI", _FakeOpenAI)
    monkeypatch.setattr(
        clients_mod, "get_bearer_token_provider", lambda *_a, **_k: lambda: "token"
    )

    client = AzureOpenAIEmbeddingClient(
        endpoint="https://example.openai.azure.com/",
        deployment="text-embedding-3-small",
    )

    kwargs = sentinel["openai_kwargs"]
    assert kwargs["azure_endpoint"] == "https://example.openai.azure.com/"
    assert kwargs["api_version"]  # populated, not empty
    assert callable(kwargs["azure_ad_token_provider"])
    assert client._deployment == "text-embedding-3-small"  # noqa: SLF001
