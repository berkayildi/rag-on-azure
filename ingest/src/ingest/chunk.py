"""Split fetched markdown sources into ``chunks.jsonl`` with metadata.

Pipeline (see ``docs/design/rag-on-azure.md`` §4.3):

1. Read ``ingest/.cache/.fetched.jsonl`` — the index of consumable sources
   produced by ``ingest.fetch``.
2. For each source, run ``MarkdownHeaderTextSplitter`` (H1/H2/H3) followed by
   ``RecursiveCharacterTextSplitter`` token-counting via tiktoken's
   ``cl100k_base`` (the encoding used by ``text-embedding-3-small``).
3. Emit one JSON line per chunk to ``ingest/.cache/chunks.jsonl``. Chunks are
   identified by ``sha256("{source_id}:{index}")[:16]`` — deterministic across
   runs given stable source bytes and splitter version.

The output is a full rewrite each run; chunking is a pure function of the
fetched markdown plus the splitter parameters here.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)
from pydantic import BaseModel

from ingest.fetch import INDEX_FILENAME, FetchedRecord

log = logging.getLogger(__name__)

HEADERS_TO_SPLIT_ON: list[tuple[str, str]] = [
    ("#", "H1"),
    ("##", "H2"),
    ("###", "H3"),
]
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
ENCODING = "cl100k_base"

CHUNKS_FILENAME = "chunks.jsonl"


class Chunk(BaseModel):
    id: str
    tenant_id: str
    source: str
    section_path: str
    chunk_text: str


def _section_path(metadata: dict[str, Any]) -> str:
    """Join H1/H2/H3 in declaration order; empty string if none precede."""
    parts = [metadata[k] for k in ("H1", "H2", "H3") if metadata.get(k)]
    return " / ".join(parts)


def _chunk_id(source_id: str, index: int) -> str:
    digest = hashlib.sha256(f"{source_id}:{index}".encode("utf-8")).hexdigest()
    return digest[:16]


def _build_splitters() -> (
    tuple[MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter]
):
    md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=HEADERS_TO_SPLIT_ON)
    text_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name=ENCODING,
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    return md_splitter, text_splitter


def _split_source(
    source_id: str,
    tenant_id: str,
    md_text: str,
    md_splitter: MarkdownHeaderTextSplitter,
    text_splitter: RecursiveCharacterTextSplitter,
) -> list[Chunk]:
    header_chunks = md_splitter.split_text(md_text)
    chunks: list[Chunk] = []
    index = 0
    for header_chunk in header_chunks:
        section_path = _section_path(header_chunk.metadata)
        for sub in text_splitter.split_text(header_chunk.page_content):
            chunks.append(
                Chunk(
                    id=_chunk_id(source_id, index),
                    tenant_id=tenant_id,
                    source=source_id,
                    section_path=section_path,
                    chunk_text=sub,
                )
            )
            index += 1
    return chunks


def _read_index(cache_dir: Path) -> list[FetchedRecord]:
    path = cache_dir / INDEX_FILENAME
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `ingest fetch` before `ingest chunk`"
        )
    records: list[FetchedRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(FetchedRecord.model_validate_json(line))
    return records


def chunk_all(cache_dir: Path) -> list[Chunk]:
    md_splitter, text_splitter = _build_splitters()
    records = _read_index(cache_dir)

    all_chunks: list[Chunk] = []
    for record in records:
        md_text = (cache_dir / record.md_path).read_text(encoding="utf-8")
        chunks = _split_source(
            record.id, record.tenant_id, md_text, md_splitter, text_splitter
        )
        all_chunks.extend(chunks)
        log.info("chunked %s into %d chunks", record.id, len(chunks))

    output = cache_dir / CHUNKS_FILENAME
    with output.open("w", encoding="utf-8") as f:
        for chunk in all_chunks:
            f.write(
                json.dumps(chunk.model_dump(), sort_keys=True, ensure_ascii=False)
                + "\n"
            )

    log.info("wrote %d chunks to %s", len(all_chunks), output)
    return all_chunks


_INGEST_DIR = Path(__file__).resolve().parent.parent.parent


def run() -> None:
    cache_dir = _INGEST_DIR / ".cache"
    chunk_all(cache_dir)
