"""Unit tests for ``ingest.fetch`` using ``httpx.MockTransport``."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from tenacity import wait_none

import ingest.fetch as fetch_mod
from ingest.fetch import (
    INDEX_FILENAME,
    FetchedRecord,
    Manifest,
    fetch_all,
    load_manifest,
)


HTML_BODY = (
    b"<html><head><title>x</title><script>x()</script></head>"
    b"<body><h1>Hello</h1><p>World</p></body></html>"
)


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Skip exponential backoff so retry tests stay sub-second."""
    monkeypatch.setattr(fetch_mod, "_RETRY_WAIT", wait_none())
    yield


def _write_manifest(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "corpus_manifest.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def _basic_manifest(extra: str = "") -> str:
    return (
        "default_tenant_id: demo\n"
        "sources:\n"
        "  - id: html-one\n"
        '    title: "HTML one"\n'
        "    url: https://example.test/one\n"
        "    format: html\n"
        "    licence_url: https://example.test/licence\n" + extra
    )


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------


def test_load_manifest_applies_default_tenant(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, _basic_manifest())
    manifest = load_manifest(path)
    resolved = manifest.resolved()
    assert len(resolved) == 1
    source, tenant_id = resolved[0]
    assert source.id == "html-one"
    assert tenant_id == "demo"


def test_per_source_tenant_id_overrides_default(tmp_path: Path) -> None:
    body = (
        "default_tenant_id: demo\n"
        "sources:\n"
        "  - id: html-a\n"
        '    title: "A"\n'
        "    url: https://example.test/a\n"
        "    format: html\n"
        "    licence_url: https://example.test/l\n"
        "    tenant_id: demo-a\n"
    )
    manifest = load_manifest(_write_manifest(tmp_path, body))
    [(_, tenant_id)] = manifest.resolved()
    assert tenant_id == "demo-a"


def test_load_manifest_rejects_unknown_format(tmp_path: Path) -> None:
    body = (
        "default_tenant_id: demo\n"
        "sources:\n"
        "  - id: bad\n"
        '    title: "B"\n'
        "    url: https://example.test/b\n"
        "    format: docx\n"
        "    licence_url: https://example.test/l\n"
    )
    with pytest.raises(ValidationError):
        load_manifest(_write_manifest(tmp_path, body))


def test_manifest_model_typing() -> None:
    """Sanity-check Pydantic Manifest construction (defensive against drift)."""
    m = Manifest.model_validate(
        {
            "default_tenant_id": "demo",
            "sources": [
                {
                    "id": "x",
                    "title": "x",
                    "url": "https://e.test/",
                    "format": "html",
                    "licence_url": "https://e.test/l",
                }
            ],
        }
    )
    assert m.sources[0].format == "html"


# ---------------------------------------------------------------------------
# Fetch behaviour
# ---------------------------------------------------------------------------


async def test_fetches_html_and_writes_markdown_plus_meta(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path, _basic_manifest())
    cache = tmp_path / ".cache"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=HTML_BODY)

    transport = httpx.MockTransport(handler)
    [result] = await fetch_all(manifest_path, cache, transport=transport)

    assert result.status == "fetched"
    assert result.id == "html-one"
    assert result.path == cache / "html-one.md"

    md_text = (cache / "html-one.md").read_text(encoding="utf-8")
    assert "# Hello" in md_text
    assert "World" in md_text
    assert "<script>" not in md_text  # script stripped

    meta = json.loads((cache / "html-one.meta.json").read_text(encoding="utf-8"))
    assert meta["id"] == "html-one"
    assert meta["url"] == "https://example.test/one"
    assert meta["format"] == "html"
    assert meta["licence_url"] == "https://example.test/licence"
    assert meta["tenant_id"] == "demo"
    assert meta["sha256"] == hashlib.sha256(HTML_BODY).hexdigest()
    assert "fetched_at" in meta


async def test_skips_pdf_without_network_call(tmp_path: Path) -> None:
    body = (
        "default_tenant_id: demo\n"
        "sources:\n"
        "  - id: pdf-only\n"
        '    title: "P"\n'
        "    url: https://example.test/p.pdf\n"
        "    format: pdf\n"
        "    licence_url: https://example.test/l\n"
    )
    manifest_path = _write_manifest(tmp_path, body)
    cache = tmp_path / ".cache"

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=b"unreached")

    transport = httpx.MockTransport(handler)
    [result] = await fetch_all(manifest_path, cache, transport=transport)

    assert result.status == "skipped_pdf"
    assert calls == []
    assert not (cache / "pdf-only.md").exists()
    assert not (cache / "pdf-only.meta.json").exists()


async def test_idempotent_when_hash_unchanged(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path, _basic_manifest())
    cache = tmp_path / ".cache"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=HTML_BODY)

    transport = httpx.MockTransport(handler)

    [first] = await fetch_all(manifest_path, cache, transport=transport)
    assert first.status == "fetched"

    md_path = cache / "html-one.md"
    meta_path = cache / "html-one.meta.json"
    md_mtime_before = md_path.stat().st_mtime_ns
    meta_mtime_before = meta_path.stat().st_mtime_ns

    [second] = await fetch_all(manifest_path, cache, transport=transport)
    assert second.status == "unchanged"
    assert second.sha256 == first.sha256
    # Files must not have been rewritten.
    assert md_path.stat().st_mtime_ns == md_mtime_before
    assert meta_path.stat().st_mtime_ns == meta_mtime_before


async def test_rewrites_when_hash_changes(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path, _basic_manifest())
    cache = tmp_path / ".cache"

    bodies = iter([HTML_BODY, b"<html><body><h1>Different</h1></body></html>"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=next(bodies))

    transport = httpx.MockTransport(handler)

    first = (await fetch_all(manifest_path, cache, transport=transport))[0]
    second = (await fetch_all(manifest_path, cache, transport=transport))[0]

    assert first.status == "fetched"
    assert second.status == "fetched"
    assert first.sha256 != second.sha256
    assert "Different" in (cache / "html-one.md").read_text(encoding="utf-8")


async def test_retries_on_5xx_then_succeeds(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path, _basic_manifest())
    cache = tmp_path / ".cache"

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return httpx.Response(503, content=b"down")
        return httpx.Response(200, content=HTML_BODY)

    transport = httpx.MockTransport(handler)
    [result] = await fetch_all(manifest_path, cache, transport=transport)

    assert call_count == 3
    assert result.status == "fetched"


async def test_retries_exhausted_returns_failed(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path, _basic_manifest())
    cache = tmp_path / ".cache"

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(500, content=b"always down")

    transport = httpx.MockTransport(handler)
    [result] = await fetch_all(manifest_path, cache, transport=transport)

    assert call_count == fetch_mod.MAX_ATTEMPTS
    assert result.status == "failed"
    assert "500" in (result.message or "")


async def test_4xx_is_fatal_no_retry(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path, _basic_manifest())
    cache = tmp_path / ".cache"

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(404, content=b"gone")

    transport = httpx.MockTransport(handler)
    [result] = await fetch_all(manifest_path, cache, transport=transport)

    assert call_count == 1  # no retries on 4xx
    assert result.status == "failed"
    assert "404" in (result.message or "")


async def test_one_source_failure_does_not_block_others(tmp_path: Path) -> None:
    body = (
        "default_tenant_id: demo\n"
        "sources:\n"
        "  - id: ok\n"
        '    title: "OK"\n'
        "    url: https://example.test/ok\n"
        "    format: html\n"
        "    licence_url: https://example.test/l\n"
        "  - id: bad\n"
        '    title: "BAD"\n'
        "    url: https://example.test/bad\n"
        "    format: html\n"
        "    licence_url: https://example.test/l\n"
    )
    manifest_path = _write_manifest(tmp_path, body)
    cache = tmp_path / ".cache"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/bad":
            return httpx.Response(404)
        return httpx.Response(200, content=HTML_BODY)

    transport = httpx.MockTransport(handler)
    results = await fetch_all(manifest_path, cache, transport=transport)

    by_id = {r.id: r for r in results}
    assert by_id["ok"].status == "fetched"
    assert by_id["bad"].status == "failed"


# ---------------------------------------------------------------------------
# .fetched.jsonl index
# ---------------------------------------------------------------------------


async def test_fetched_jsonl_lists_only_consumable_sources(tmp_path: Path) -> None:
    body = (
        "default_tenant_id: demo\n"
        "sources:\n"
        "  - id: html-ok\n"
        '    title: "OK"\n'
        "    url: https://example.test/ok\n"
        "    format: html\n"
        "    licence_url: https://example.test/l\n"
        "  - id: html-bad\n"
        '    title: "BAD"\n'
        "    url: https://example.test/bad\n"
        "    format: html\n"
        "    licence_url: https://example.test/l\n"
        "  - id: pdf-skip\n"
        '    title: "PDF"\n'
        "    url: https://example.test/p.pdf\n"
        "    format: pdf\n"
        "    licence_url: https://example.test/l\n"
    )
    manifest_path = _write_manifest(tmp_path, body)
    cache = tmp_path / ".cache"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/bad":
            return httpx.Response(404)
        return httpx.Response(200, content=HTML_BODY)

    transport = httpx.MockTransport(handler)
    await fetch_all(manifest_path, cache, transport=transport)

    index_path = cache / INDEX_FILENAME
    assert index_path.exists()

    lines = [line for line in index_path.read_text().splitlines() if line.strip()]
    records = [FetchedRecord.model_validate_json(line) for line in lines]

    ids = {r.id for r in records}
    assert ids == {"html-ok"}  # bad/failed and pdf-skipped both omitted

    [ok] = records
    assert ok.tenant_id == "demo"
    assert ok.format == "html"
    assert ok.md_path == "html-ok.md"
    assert ok.sha256  # populated, not empty


async def test_fetched_jsonl_includes_unchanged_on_rerun(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path, _basic_manifest())
    cache = tmp_path / ".cache"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=HTML_BODY)

    transport = httpx.MockTransport(handler)

    await fetch_all(manifest_path, cache, transport=transport)
    [second] = await fetch_all(manifest_path, cache, transport=transport)
    assert second.status == "unchanged"

    lines = [
        line
        for line in (cache / INDEX_FILENAME).read_text().splitlines()
        if line.strip()
    ]
    records = [FetchedRecord.model_validate_json(line) for line in lines]
    assert [r.id for r in records] == ["html-one"]
