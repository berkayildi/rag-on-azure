"""Shared unit-test fixtures for the rag_on_azure package.

``FakeSearchClient`` here is a *minimal* in-memory stand-in for
``azure.search.documents.aio.SearchClient`` — enough to exercise the
``TenantAwareSearchClient`` boundary in unit tests, including the two
audit-grade ones in test_search.py. It is NOT a full Azure Search
emulator.

It honours simple OData ``field eq 'value'`` clauses joined by ``and``
because that's exactly what ``_build_filter`` produces — the parser
deliberately rejects anything more exotic so we don't accidentally let
a more permissive parser hide a bug in real filter composition.

``FakeLLMClient`` is the ``LLMClient``-Protocol stand-in used by the
graph-node unit tests. ``complete()`` returns whatever the test queues
into ``complete_responses`` (popped FIFO); ``embed()`` returns
deterministic canned vectors. Calls are recorded so tests can assert
prompt + schema wiring.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from rag_on_azure.models import Message


class _FakeAsyncIterator:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = list(items)

    def __aiter__(self) -> _FakeAsyncIterator:
        return self

    async def __anext__(self) -> dict[str, Any]:
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


class _FakeUploadResult:
    """Mimics azure.search.documents.IndexingResult enough for ingest."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.succeeded = True
        self.error_message: str | None = None


_CLAUSE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s+eq\s+'(.*)'$")


def _parse_filter(filter_str: str | None) -> list[tuple[str, str]]:
    """Parse `field eq 'X' and field2 eq 'Y'` into [(field, value), ...].

    Single quotes inside values are doubled per OData; we un-double on
    parse so the in-memory equality check matches real values.
    """
    if not filter_str:
        return []
    clauses: list[tuple[str, str]] = []
    for raw in filter_str.split(" and "):
        match = _CLAUSE_RE.match(raw.strip())
        if not match:
            raise ValueError(
                f"FakeSearchClient cannot parse filter clause: {raw!r}. "
                "This fake only handles `field eq 'value'` joined by ` and `."
            )
        field, value = match.group(1), match.group(2).replace("''", "'")
        clauses.append((field, value))
    return clauses


def _matches(doc: dict[str, Any], clauses: list[tuple[str, str]]) -> bool:
    return all(doc.get(field) == value for field, value in clauses)


class FakeSearchClient:
    """In-memory fake exercising the TenantAwareSearchClient boundary."""

    def __init__(
        self,
        docs: list[dict[str, Any]] | None = None,
        get_document_count_raises: Exception | None = None,
    ) -> None:
        self._store: list[dict[str, Any]] = [dict(d) for d in (docs or [])]
        self.search_calls: list[dict[str, Any]] = []
        self.upload_calls: list[list[dict[str, Any]]] = []
        self.closed: bool = False
        self.close_calls: int = 0
        self._get_document_count_raises = get_document_count_raises
        self.get_document_count_calls: int = 0

    async def search(
        self,
        *,
        search_text: str | None = None,
        vector_queries: list[Any] | None = None,
        filter: str | None = None,  # noqa: A002 - matches Azure SDK signature
        select: list[str] | None = None,
        top: int | None = None,
        **_extra: Any,
    ) -> _FakeAsyncIterator:
        self.search_calls.append(
            {
                "search_text": search_text,
                "vector_queries": vector_queries,
                "filter": filter,
                "select": select,
                "top": top,
            }
        )
        clauses = _parse_filter(filter)
        matched = [dict(d) for d in self._store if _matches(d, clauses)]
        # Synthesize @search.score for any doc that doesn't have one.
        for i, doc in enumerate(matched):
            doc.setdefault("@search.score", 1.0 - i * 0.01)
        if top is not None:
            matched = matched[:top]
        return _FakeAsyncIterator(matched)

    async def get_document_count(self) -> int:
        self.get_document_count_calls += 1
        if self._get_document_count_raises is not None:
            raise self._get_document_count_raises
        return len(self._store)

    async def upload_documents(
        self, *, documents: list[dict[str, Any]]
    ) -> list[_FakeUploadResult]:
        copied = [dict(d) for d in documents]
        self.upload_calls.append(copied)
        for doc in copied:
            self._store.append(doc)
        return [_FakeUploadResult(key=d["id"]) for d in copied]

    async def close(self) -> None:
        self.closed = True
        self.close_calls += 1


class FakeLLMClient:
    """In-memory LLMClient stand-in for graph-node unit tests.

    ``complete_responses`` is a FIFO queue: each call to ``complete()``
    pops one item. This lets a test set up a multi-step interaction
    (e.g. a fabricated-citation retry) by queuing two responses.
    ``embed()`` returns deterministic canned vectors of ``embed_dim``
    floats — content doesn't matter to the search-client boundary
    because the FakeSearchClient ignores vectors.
    """

    def __init__(
        self,
        complete_responses: list[Any] | None = None,
        embed_dim: int = 1536,
        ping_raises: Exception | None = None,
    ) -> None:
        self._complete_responses: list[Any] = list(complete_responses or [])
        self._embed_dim = embed_dim
        self._ping_raises = ping_raises
        self.complete_calls: list[dict[str, Any]] = []
        self.embed_calls: list[list[str]] = []
        self.ping_calls: int = 0

    async def ping(self) -> None:
        self.ping_calls += 1
        if self._ping_raises is not None:
            raise self._ping_raises

    async def complete(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
    ) -> Any:
        self.complete_calls.append({"messages": list(messages), "schema": schema})
        if not self._complete_responses:
            raise RuntimeError(
                "FakeLLMClient: no complete_responses queued for this call"
            )
        return self._complete_responses.pop(0)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        return [[0.1] * self._embed_dim for _ in texts]


class FakeKeyVaultClient:
    """In-memory KeyVaultClient stand-in for route + auth tests.

    ``signing_key`` is whatever shape the test wants — a PEM string for
    signature-verification tests, an empty string for /readyz happy-path
    tests, an arbitrary value when only ping behavior matters. The two
    optional ``*_raises`` hooks let failure-path tests assert specific
    HTTP status mappings without bringing up real Key Vault.
    """

    def __init__(
        self,
        signing_key: str = "",
        ping_raises: Exception | None = None,
        get_signing_key_raises: Exception | None = None,
    ) -> None:
        self._signing_key = signing_key
        self._ping_raises = ping_raises
        self._get_signing_key_raises = get_signing_key_raises
        self.ping_calls: int = 0
        self.get_signing_key_calls: int = 0

    async def ping(self) -> None:
        self.ping_calls += 1
        if self._ping_raises is not None:
            raise self._ping_raises

    async def get_signing_key(self) -> str:
        self.get_signing_key_calls += 1
        if self._get_signing_key_raises is not None:
            raise self._get_signing_key_raises
        return self._signing_key

    async def close(self) -> None:
        pass
