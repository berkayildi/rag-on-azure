"""Unit tests for AzureOpenAIClient + OpenAIDirectClient + LLMClient Protocol."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from openai import RateLimitError
from pydantic import BaseModel
from tenacity import wait_none

import rag_on_azure.clients.llm as llm_mod
from rag_on_azure.clients.llm import (
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_TPM,
    TOKENS_PER_CHUNK_ESTIMATE,
    AzureOpenAIClient,
    OpenAIDirectClient,
)
from rag_on_azure.models import Message


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeEmbedding:
    embedding: list[float]


@dataclass
class _FakeEmbeddingResponse:
    data: list[_FakeEmbedding]


@dataclass
class _FakeEmbedCall:
    model: str
    input: list[str]


class _FakeEmbeddingsAPI:
    def __init__(
        self,
        *,
        embedding_dim: int = 1536,
        exceptions: list[Exception] | None = None,
    ) -> None:
        self.calls: list[_FakeEmbedCall] = []
        self._dim = embedding_dim
        self._exceptions = list(exceptions or [])

    async def create(self, *, model: str, input: list[str]) -> _FakeEmbeddingResponse:
        self.calls.append(_FakeEmbedCall(model=model, input=list(input)))
        if self._exceptions:
            raise self._exceptions.pop(0)
        return _FakeEmbeddingResponse(
            data=[_FakeEmbedding(embedding=[0.1] * self._dim) for _ in input]
        )


@dataclass
class _FakeMessage:
    content: str | None = None
    parsed: Any = None


@dataclass
class _FakeChoice:
    message: _FakeMessage


@dataclass
class _FakeCompletion:
    choices: list[_FakeChoice]


@dataclass
class _FakeChatCall:
    model: str
    messages: list[dict[str, str]]
    response_format: type | None = None


class _FakeChatCompletions:
    def __init__(
        self, *, content: str = "default response", parsed: Any = None
    ) -> None:
        self._content = content
        self._parsed = parsed
        self.create_calls: list[_FakeChatCall] = []
        self.parse_calls: list[_FakeChatCall] = []

    async def create(
        self, *, model: str, messages: list[dict[str, str]]
    ) -> _FakeCompletion:
        self.create_calls.append(_FakeChatCall(model=model, messages=list(messages)))
        return _FakeCompletion(
            choices=[_FakeChoice(message=_FakeMessage(content=self._content))]
        )

    async def parse(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        response_format: type,
    ) -> _FakeCompletion:
        self.parse_calls.append(
            _FakeChatCall(
                model=model, messages=list(messages), response_format=response_format
            )
        )
        return _FakeCompletion(
            choices=[_FakeChoice(message=_FakeMessage(parsed=self._parsed))]
        )


@dataclass
class _FakeChat:
    completions: _FakeChatCompletions


class _FakeAsyncOpenAI:
    def __init__(
        self,
        *,
        embeddings: _FakeEmbeddingsAPI | None = None,
        chat: _FakeChat | None = None,
    ) -> None:
        self.embeddings = embeddings or _FakeEmbeddingsAPI()
        self.chat = chat or _FakeChat(completions=_FakeChatCompletions())
        self.closed = False
        self.close_calls = 0

    async def close(self) -> None:
        self.closed = True
        self.close_calls += 1


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Skip exponential backoff so retry tests stay sub-second."""
    monkeypatch.setattr(llm_mod, "_RETRY_WAIT", wait_none())
    yield


@pytest.fixture(autouse=True)
def _no_throttle_sleep(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Replace the per-batch throttle sleep with a no-op for most tests.
    The dedicated throttle test re-monkeypatches this with a recorder."""

    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(llm_mod, "_sleep_between_batches", _instant)
    yield


# ---------------------------------------------------------------------------
# Embed — batching + ordering
# ---------------------------------------------------------------------------


async def test_embed_batches_of_16() -> None:
    inner = _FakeAsyncOpenAI()
    client = AzureOpenAIClient(
        embedding_deployment="text-embedding-3-small",
        inner=inner,  # type: ignore[arg-type]
    )

    embeddings = await client.embed([f"text {i}" for i in range(35)])

    assert len(embeddings) == 35
    assert [len(c.input) for c in inner.embeddings.calls] == [16, 16, 3]
    assert EMBEDDING_BATCH_SIZE == 16


async def test_embed_returns_one_vector_per_input() -> None:
    inner = _FakeAsyncOpenAI()
    client = AzureOpenAIClient(
        embedding_deployment="d",
        inner=inner,  # type: ignore[arg-type]
    )
    embeddings = await client.embed(["a", "b", "c"])
    assert len(embeddings) == 3
    assert all(len(v) == 1536 for v in embeddings)


async def test_embed_passes_deployment_name() -> None:
    inner = _FakeAsyncOpenAI()
    client = AzureOpenAIClient(
        embedding_deployment="text-embedding-3-small",
        inner=inner,  # type: ignore[arg-type]
    )
    await client.embed(["a"])
    assert inner.embeddings.calls[0].model == "text-embedding-3-small"


async def test_embed_empty_input_makes_no_calls() -> None:
    inner = _FakeAsyncOpenAI()
    client = AzureOpenAIClient(
        embedding_deployment="d",
        inner=inner,  # type: ignore[arg-type]
    )
    embeddings = await client.embed([])
    assert embeddings == []
    assert inner.embeddings.calls == []


# ---------------------------------------------------------------------------
# Embed — throttle (D31)
# ---------------------------------------------------------------------------


async def test_embed_paces_under_tpm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-batch sleep durations sum to the budget implied by TPM ceiling.

    35 inputs → 3 batches → 2 inter-batch sleeps (no sleep before the
    first batch, no sleep after the last). Each sleep should be
    ``EMBEDDING_BATCH_SIZE * TOKENS_PER_CHUNK_ESTIMATE / TPM * 60``.
    """
    sleep_calls: list[float] = []

    async def _recorder(seconds: float) -> None:
        sleep_calls.append(seconds)

    # Override the autouse-no-throttle fixture for this test.
    monkeypatch.setattr(llm_mod, "_sleep_between_batches", _recorder)

    inner = _FakeAsyncOpenAI()
    client = AzureOpenAIClient(
        embedding_deployment="d",
        inner=inner,  # type: ignore[arg-type]
        embedding_tpm=EMBEDDING_TPM,
    )

    await client.embed([f"text {i}" for i in range(35)])

    expected_per_batch = (
        EMBEDDING_BATCH_SIZE * TOKENS_PER_CHUNK_ESTIMATE / EMBEDDING_TPM * 60
    )
    assert len(sleep_calls) == 2  # N-1 inter-batch sleeps for N batches
    for s in sleep_calls:
        assert abs(s - expected_per_batch) < 1e-9


async def test_embed_throttle_scales_with_custom_tpm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Higher TPM → shorter inter-batch sleep. Anchors the ``embedding_tpm``
    constructor parameter as the load-bearing knob for forks on dedicated
    SKUs."""
    sleep_calls: list[float] = []

    async def _recorder(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(llm_mod, "_sleep_between_batches", _recorder)

    inner = _FakeAsyncOpenAI()
    client = AzureOpenAIClient(
        embedding_deployment="d",
        inner=inner,  # type: ignore[arg-type]
        embedding_tpm=120_000,  # 5x default
    )
    await client.embed(["a"] * 17)  # 2 batches = 1 sleep
    assert len(sleep_calls) == 1
    expected = EMBEDDING_BATCH_SIZE * TOKENS_PER_CHUNK_ESTIMATE / 120_000 * 60
    assert abs(sleep_calls[0] - expected) < 1e-9


async def test_embed_no_sleep_when_single_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """≤16 inputs → 1 batch → 0 inter-batch sleeps."""
    sleep_calls: list[float] = []

    async def _recorder(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(llm_mod, "_sleep_between_batches", _recorder)

    inner = _FakeAsyncOpenAI()
    client = AzureOpenAIClient(
        embedding_deployment="d",
        inner=inner,  # type: ignore[arg-type]
    )
    await client.embed(["a"] * 16)
    assert sleep_calls == []


# ---------------------------------------------------------------------------
# Embed — retry (defence in depth)
# ---------------------------------------------------------------------------


def _rate_limit_error(message: str = "rate limited") -> RateLimitError:
    """Construct a RateLimitError with a minimal httpx.Response."""
    return RateLimitError(
        message=message,
        response=httpx.Response(
            429, request=httpx.Request("POST", "https://example.test/x")
        ),
        body=None,
    )


async def test_retry_on_rate_limit_error() -> None:
    """openai.RateLimitError on first attempt, success on retry — the
    defence-in-depth contract that catches transient bursts when the
    pre-pacing throttle isn't enough (e.g. shared-bucket spikes)."""
    inner = _FakeAsyncOpenAI(
        embeddings=_FakeEmbeddingsAPI(exceptions=[_rate_limit_error()])
    )
    client = AzureOpenAIClient(
        embedding_deployment="d",
        inner=inner,  # type: ignore[arg-type]
    )
    embeddings = await client.embed(["a"])
    assert len(embeddings) == 1
    assert len(inner.embeddings.calls) == 2  # one failure + one success


async def test_retry_exhausted_propagates() -> None:
    inner = _FakeAsyncOpenAI(
        embeddings=_FakeEmbeddingsAPI(
            exceptions=[
                _rate_limit_error("a"),
                _rate_limit_error("b"),
                _rate_limit_error("c"),
            ]
        )
    )
    client = AzureOpenAIClient(
        embedding_deployment="d",
        inner=inner,  # type: ignore[arg-type]
    )
    with pytest.raises(RateLimitError):
        await client.embed(["a"])
    assert len(inner.embeddings.calls) == 3  # MAX_ATTEMPTS


async def test_non_retryable_exception_propagates_immediately() -> None:
    """Non-retry-typed exceptions must not be retried."""

    class _Fatal(Exception):
        pass

    inner = _FakeAsyncOpenAI(embeddings=_FakeEmbeddingsAPI(exceptions=[_Fatal("auth")]))
    client = AzureOpenAIClient(
        embedding_deployment="d",
        inner=inner,  # type: ignore[arg-type]
    )
    with pytest.raises(_Fatal):
        await client.embed(["a"])
    assert len(inner.embeddings.calls) == 1


# ---------------------------------------------------------------------------
# Complete — schema vs no-schema paths
# ---------------------------------------------------------------------------


class _AnswerSchema(BaseModel):
    text: str
    cited_chunk_ids: list[str]


async def test_complete_without_schema_returns_string() -> None:
    chat = _FakeChat(completions=_FakeChatCompletions(content="hello, world"))
    inner = _FakeAsyncOpenAI(chat=chat)
    client = AzureOpenAIClient(
        embedding_deployment="e",
        chat_deployment="gpt-4o",
        inner=inner,  # type: ignore[arg-type]
    )

    out = await client.complete([Message(role="user", content="hi")])
    assert out == "hello, world"
    assert len(chat.completions.create_calls) == 1
    assert chat.completions.create_calls[0].model == "gpt-4o"
    assert chat.completions.create_calls[0].messages == [
        {"role": "user", "content": "hi"}
    ]
    assert chat.completions.parse_calls == []


async def test_complete_with_schema_returns_pydantic_instance() -> None:
    expected = _AnswerSchema(text="answer body", cited_chunk_ids=["c1", "c2"])
    chat = _FakeChat(completions=_FakeChatCompletions(parsed=expected))
    inner = _FakeAsyncOpenAI(chat=chat)
    client = AzureOpenAIClient(
        embedding_deployment="e",
        chat_deployment="gpt-4o",
        inner=inner,  # type: ignore[arg-type]
    )

    out = await client.complete(
        [Message(role="user", content="q")], schema=_AnswerSchema
    )
    assert isinstance(out, _AnswerSchema)
    assert out == expected
    assert len(chat.completions.parse_calls) == 1
    assert chat.completions.parse_calls[0].response_format is _AnswerSchema
    assert chat.completions.create_calls == []


async def test_complete_without_chat_deployment_raises() -> None:
    inner = _FakeAsyncOpenAI()
    client = AzureOpenAIClient(
        embedding_deployment="e",
        inner=inner,  # type: ignore[arg-type]
        # chat_deployment intentionally omitted
    )
    with pytest.raises(RuntimeError, match="chat_deployment"):
        await client.complete([Message(role="user", content="x")])


# ---------------------------------------------------------------------------
# Lifecycle / construction
# ---------------------------------------------------------------------------


async def test_close_closes_inner_client() -> None:
    inner = _FakeAsyncOpenAI()
    client = AzureOpenAIClient(
        embedding_deployment="d",
        inner=inner,  # type: ignore[arg-type]
    )
    await client.close()
    assert inner.closed is True
    assert inner.close_calls == 1


async def test_close_idempotent() -> None:
    """Calling close twice must not raise and must not double-close inner."""
    inner = _FakeAsyncOpenAI()
    client = AzureOpenAIClient(
        embedding_deployment="d",
        inner=inner,  # type: ignore[arg-type]
    )
    await client.close()
    await client.close()  # second call is a no-op
    assert inner.close_calls == 1


async def test_async_context_manager_closes_on_exit() -> None:
    inner = _FakeAsyncOpenAI()
    async with AzureOpenAIClient(
        embedding_deployment="d",
        inner=inner,  # type: ignore[arg-type]
    ):
        pass
    assert inner.closed is True


def test_endpoint_required_when_inner_not_supplied() -> None:
    with pytest.raises(ValueError, match="endpoint"):
        AzureOpenAIClient(embedding_deployment="d")


def test_constructed_inner_is_attached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Construction with endpoint+deployment builds an AsyncAzureOpenAI."""
    sentinel: dict[str, Any] = {}

    class _FakeCred:
        async def close(self) -> None:
            sentinel["cred_closed"] = True

    class _FakeOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            sentinel["openai_kwargs"] = kwargs

        async def close(self) -> None:
            sentinel["openai_closed"] = True

    monkeypatch.setattr(llm_mod, "DefaultAzureCredential", _FakeCred)
    monkeypatch.setattr(llm_mod, "AsyncAzureOpenAI", _FakeOpenAI)
    monkeypatch.setattr(
        llm_mod, "get_bearer_token_provider", lambda *_a, **_k: lambda: "tok"
    )

    client = AzureOpenAIClient(
        endpoint="https://example.openai.azure.com/",
        embedding_deployment="text-embedding-3-small",
        chat_deployment="gpt-4o",
    )
    kwargs = sentinel["openai_kwargs"]
    assert kwargs["azure_endpoint"] == "https://example.openai.azure.com/"
    assert kwargs["api_version"]
    assert callable(kwargs["azure_ad_token_provider"])
    assert client._embedding_deployment == "text-embedding-3-small"  # noqa: SLF001
    assert client._chat_deployment == "gpt-4o"  # noqa: SLF001


# ---------------------------------------------------------------------------
# OpenAIDirectClient stub (D6)
# ---------------------------------------------------------------------------


async def test_open_ai_direct_client_complete_raises() -> None:
    client = OpenAIDirectClient()
    with pytest.raises(NotImplementedError, match="design spec"):
        await client.complete([Message(role="user", content="x")])


async def test_open_ai_direct_client_embed_raises() -> None:
    client = OpenAIDirectClient()
    with pytest.raises(NotImplementedError, match="design spec"):
        await client.embed(["a"])


def test_open_ai_direct_client_satisfies_llm_protocol() -> None:
    """Stub structurally implements the LLMClient Protocol — mypy catches
    polymorphic misuse at type-check time. Runtime: just confirm the
    expected methods exist with the expected signatures."""
    from rag_on_azure.clients.llm import LLMClient

    def _accepts_llm_client(_c: LLMClient) -> None: ...

    _accepts_llm_client(OpenAIDirectClient())  # mypy + runtime check
