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
"""

from __future__ import annotations

import re
from typing import Any


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

    def __init__(self, docs: list[dict[str, Any]] | None = None) -> None:
        self._store: list[dict[str, Any]] = [dict(d) for d in (docs or [])]
        self.search_calls: list[dict[str, Any]] = []
        self.upload_calls: list[list[dict[str, Any]]] = []
        self.closed: bool = False
        self.close_calls: int = 0

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
