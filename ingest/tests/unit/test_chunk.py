"""Unit tests for ``ingest.chunk``."""

from __future__ import annotations

from pathlib import Path

import tiktoken

from ingest.chunk import (
    CHUNK_SIZE,
    CHUNKS_FILENAME,
    Chunk,
    _chunk_id,
    _section_path,
    chunk_all,
)
from ingest.fetch import INDEX_FILENAME, FetchedRecord


def _seed(
    cache: Path,
    sources: list[tuple[str, str, str]],
    *,
    tenant_id: str = "demo",
) -> None:
    """Write a fake fetch output: a markdown file plus an index entry per source."""
    cache.mkdir(parents=True, exist_ok=True)
    index_lines: list[str] = []
    for source_id, md_filename, md_text in sources:
        (cache / md_filename).write_text(md_text, encoding="utf-8")
        sha = "0" * 64  # placeholder; chunk.py doesn't validate it
        record = FetchedRecord(
            id=source_id,
            tenant_id=tenant_id,
            format="html",
            md_path=md_filename,
            sha256=sha,
        )
        index_lines.append(record.model_dump_json())
    (cache / INDEX_FILENAME).write_text("\n".join(index_lines) + "\n", encoding="utf-8")


def _read_chunks(cache: Path) -> list[Chunk]:
    raw = (cache / CHUNKS_FILENAME).read_text(encoding="utf-8").splitlines()
    return [Chunk.model_validate_json(line) for line in raw if line.strip()]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_section_path_joins_h1_h2_h3() -> None:
    assert _section_path({"H1": "A", "H2": "B", "H3": "C"}) == "A / B / C"


def test_section_path_empty_when_no_headings() -> None:
    assert _section_path({}) == ""


def test_section_path_skips_missing_levels() -> None:
    assert _section_path({"H1": "A", "H3": "C"}) == "A / C"


def test_chunk_id_is_deterministic_and_short() -> None:
    a = _chunk_id("foo", 0)
    b = _chunk_id("foo", 0)
    c = _chunk_id("foo", 1)
    d = _chunk_id("bar", 0)
    assert a == b
    assert a != c
    assert a != d
    assert len(a) == 16
    assert all(ch in "0123456789abcdef" for ch in a)


# ---------------------------------------------------------------------------
# chunk_all integration over synthetic fixtures
# ---------------------------------------------------------------------------


def test_chunks_basic_markdown(tmp_path: Path) -> None:
    cache = tmp_path / ".cache"
    md = "# Title\n\nSome introductory body text under the heading.\n"
    _seed(cache, [("doc-a", "doc-a.md", md)])

    chunks = chunk_all(cache)
    assert len(chunks) >= 1
    assert chunks[0].source == "doc-a"
    assert chunks[0].tenant_id == "demo"
    assert chunks[0].section_path == "Title"
    assert "introductory body text" in chunks[0].chunk_text
    assert (cache / CHUNKS_FILENAME).exists()


def test_section_path_preserves_heading_hierarchy(tmp_path: Path) -> None:
    cache = tmp_path / ".cache"
    md = (
        "# Top\n\n"
        "intro under top\n\n"
        "## Sub\n\n"
        "body of sub\n\n"
        "### Leaf\n\n"
        "deepest body content\n"
    )
    _seed(cache, [("doc-a", "doc-a.md", md)])

    chunks = chunk_all(cache)
    by_text = {c.chunk_text.strip(): c.section_path for c in chunks}

    # The H1 chunk is just the H1 body, H2 holds H1/H2, H3 holds H1/H2/H3.
    matched = {path for text, path in by_text.items()}
    assert "Top" in matched
    assert "Top / Sub" in matched
    assert "Top / Sub / Leaf" in matched


def test_pre_heading_text_has_empty_section_path(tmp_path: Path) -> None:
    """Anchor: text before the first heading must surface as section_path == ''.

    Guards against a future contributor adding a 'fall back to source title'
    convenience that would silently change every pre-heading chunk's id.
    """
    cache = tmp_path / ".cache"
    md = (
        "preamble paragraph that lives before any heading\n\n"
        "# Heading\n\n"
        "body under heading\n"
    )
    _seed(cache, [("doc-a", "doc-a.md", md)])

    chunks = chunk_all(cache)
    pre = [c for c in chunks if "preamble" in c.chunk_text]
    assert pre, "expected a chunk for the pre-heading text"
    assert all(c.section_path == "" for c in pre)


def test_chunk_ids_are_deterministic_across_runs(tmp_path: Path) -> None:
    cache = tmp_path / ".cache"
    md = "# Heading\n\nbody one\n\n## Sub\n\nbody two\n"
    _seed(cache, [("doc-a", "doc-a.md", md)])

    first = chunk_all(cache)
    # Chunk_all rewrites chunks.jsonl; remove it to confirm second run
    # reproduces from source, not from output cache.
    (cache / CHUNKS_FILENAME).unlink()
    second = chunk_all(cache)

    assert [c.id for c in first] == [c.id for c in second]
    assert [c.chunk_text for c in first] == [c.chunk_text for c in second]


def test_no_chunk_exceeds_chunk_size_tokens(tmp_path: Path) -> None:
    # ~4000 tokens worth of repeated paragraphs forces multiple splits.
    paragraph = ("token " * 200).strip()
    md = "# Big\n\n" + "\n\n".join([paragraph] * 30) + "\n"
    cache = tmp_path / ".cache"
    _seed(cache, [("big", "big.md", md)])

    chunks = chunk_all(cache)
    assert len(chunks) > 1, "fixture must produce multiple chunks"

    encoder = tiktoken.get_encoding("cl100k_base")
    for chunk in chunks:
        assert len(encoder.encode(chunk.chunk_text)) <= CHUNK_SIZE


def test_chunks_jsonl_is_full_rewrite(tmp_path: Path) -> None:
    cache = tmp_path / ".cache"
    _seed(cache, [("doc-a", "doc-a.md", "# A\nbody a\n")])
    chunk_all(cache)
    first = _read_chunks(cache)

    # Replace the corpus and re-run; previous source's chunks must vanish.
    _seed(cache, [("doc-b", "doc-b.md", "# B\nbody b\n")])
    chunk_all(cache)
    second = _read_chunks(cache)

    assert {c.source for c in first} == {"doc-a"}
    assert {c.source for c in second} == {"doc-b"}


def test_missing_index_raises(tmp_path: Path) -> None:
    cache = tmp_path / ".cache"
    cache.mkdir()
    try:
        chunk_all(cache)
    except FileNotFoundError as exc:
        assert INDEX_FILENAME in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError when index is missing")
