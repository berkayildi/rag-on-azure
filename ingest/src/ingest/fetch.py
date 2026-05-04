"""Fetch corpus sources declared in ``corpus_manifest.yaml`` to ``ingest/.cache/``.

Behaviour (see ``docs/design/rag-on-azure.md`` §4.2):

- HTML sources are converted to Markdown via ``markdownify``.
- PDF sources are extracted to Markdown via ``pypdf``, page-by-page, joined
  with ``---`` horizontal rules. Each page is prefixed with an HTML comment
  ``<!-- page N -->`` — invisible to renderers and to the chunk splitters,
  but greppable from chunk text at retrieval time so the generate node can
  cite page numbers (e.g. "FCA PS26/3 §1.2 (p.42)"). Routing is
  content-driven: ``application/pdf`` Content-Type or a URL ending in
  ``.pdf`` selects the PDF path regardless of the manifest's ``format``
  field (which is preserved as provenance only).
- Each fetched source produces ``{cache}/{id}.md`` plus a ``{id}.meta.json``
  sidecar holding url, format, licence, tenant_id, sha256, fetched_at.
- Re-runs are idempotent: when the SHA-256 of the freshly fetched bytes matches
  the previous run's recorded hash and the markdown file is still present, the
  files are not rewritten.

The async surface (``fetch_all``) accepts an injectable ``httpx`` transport so
unit tests can use ``httpx.MockTransport`` without touching the network.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
import pypdf
import yaml
from markdownify import markdownify  # type: ignore[import-untyped]
from pydantic import BaseModel, HttpUrl
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)

USER_AGENT = "rag-on-azure-ingest/0.0 (+https://github.com/berkayildi/rag-on-azure)"
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
HTTP_LIMITS = httpx.Limits(max_connections=4, max_keepalive_connections=4)
MAX_ATTEMPTS = 3

# Module-level so tests can monkeypatch to wait_none() and avoid real backoff.
_RETRY_WAIT = wait_exponential(multiplier=1, min=1, max=8)
_RETRY_STOP = stop_after_attempt(MAX_ATTEMPTS)

SourceFormat = Literal["html", "pdf"]
FetchStatus = Literal["fetched", "unchanged", "failed"]
INDEX_FILENAME = ".fetched.jsonl"


class PdfParseError(Exception):
    """Raised when a PDF cannot be parsed at all (corrupt, encrypted, or every
    page raises during text extraction). Caught by ``_fetch_one`` and surfaced
    as ``FetchResult(status='failed')`` so the pipeline continues."""


class Source(BaseModel):
    id: str
    title: str
    url: HttpUrl
    format: SourceFormat
    licence_url: HttpUrl
    tenant_id: str | None = None


class Manifest(BaseModel):
    default_tenant_id: str
    sources: list[Source]

    def resolved(self) -> list[tuple[Source, str]]:
        """Pair each source with its effective tenant_id."""
        return [(s, s.tenant_id or self.default_tenant_id) for s in self.sources]


class FetchResult(BaseModel):
    id: str
    status: FetchStatus
    sha256: str | None = None
    path: Path | None = None
    message: str | None = None


class FetchedRecord(BaseModel):
    """One row of ``.fetched.jsonl``: an index of consumable sources for chunk.py.

    Written only for sources whose status is ``fetched`` or ``unchanged``.
    Skipped (PDF) and failed sources are omitted by design — downstream phases
    should never see them.
    """

    id: str
    tenant_id: str
    format: SourceFormat
    md_path: str  # filename, relative to cache_dir
    sha256: str


def load_manifest(path: Path) -> Manifest:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Manifest.model_validate(raw)


def _should_retry(exc: BaseException) -> bool:
    """Retry on 5xx and transient transport / timeout errors only. 4xx is fatal."""
    if isinstance(exc, httpx.HTTPStatusError):
        return 500 <= exc.response.status_code < 600
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


async def _http_get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    async for attempt in AsyncRetrying(
        retry=retry_if_exception(_should_retry),
        stop=_RETRY_STOP,
        wait=_RETRY_WAIT,
        reraise=True,
    ):
        with attempt:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
            return response
    raise RuntimeError(  # pragma: no cover
        "unreachable: AsyncRetrying exited without success or raise"
    )


def _html_to_markdown(content: bytes) -> str:
    text = content.decode("utf-8", errors="replace")
    converted: str = markdownify(text, heading_style="ATX", strip=["script", "style"])
    return converted.strip() + "\n"


def _pdf_to_markdown(content: bytes) -> str:
    """Extract PDF text page-by-page; raise ``PdfParseError`` on total failure.

    Each successfully-extracted page is prefixed with ``<!-- page N -->`` so
    citations can recover the page number from chunk text downstream. Pages
    are joined with ``---`` (markdown horizontal rule) — strong paragraph
    boundary for ``RecursiveCharacterTextSplitter`` without registering as a
    heading in ``MarkdownHeaderTextSplitter`` (H1/H2/H3 only). Pages whose
    individual ``extract_text`` raises are logged and skipped; total failure
    (unreadable file, or 0/N pages extractable) raises.
    """
    try:
        reader = pypdf.PdfReader(io.BytesIO(content))
    except Exception as exc:
        raise PdfParseError(f"could not open pdf: {exc}") from exc

    total = len(reader.pages)
    formatted: list[str] = []
    failed_pages: list[int] = []
    for index, page in enumerate(reader.pages):
        page_number = index + 1
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # pypdf raises a wide variety of types
            log.warning("pdf page %d extract failed: %s", page_number, exc)
            failed_pages.append(page_number)
            continue
        text = text.strip()
        if not text:
            # Image-only or empty page — count as failed for visibility but
            # don't abort; many PDFs have blank cover/back pages.
            failed_pages.append(page_number)
            continue
        formatted.append(f"<!-- page {page_number} -->\n\n{text}")

    if not formatted:
        raise PdfParseError(f"pdf {total} pages, 0 extractable")

    if failed_pages:
        log.info(
            "pdf extracted %d/%d pages (skipped: %s)",
            len(formatted),
            total,
            failed_pages,
        )

    return "\n\n---\n\n".join(formatted) + "\n"


def _is_pdf_response(source: Source, response: httpx.Response) -> bool:
    """Content-driven dispatch: either Content-Type or URL suffix selects PDF.

    Manifest's ``source.format`` is *not* consulted — a .pdf URL that 302s to
    an HTML error page should be parsed as HTML (or fail loudly via pypdf's
    parser), not crash by being force-fed to pypdf. Likewise an .html URL
    serving a PDF body should be parsed as PDF.
    """
    content_type = response.headers.get("content-type", "").lower()
    url_path = str(source.url).lower().split("?", 1)[0]
    return "application/pdf" in content_type or url_path.endswith(".pdf")


def _read_meta(meta_path: Path) -> dict[str, Any] | None:
    if not meta_path.exists():
        return None
    try:
        loaded: Any = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


async def _fetch_one(
    source: Source,
    tenant_id: str,
    client: httpx.AsyncClient,
    cache_dir: Path,
) -> FetchResult:
    md_path = cache_dir / f"{source.id}.md"
    meta_path = cache_dir / f"{source.id}.meta.json"

    try:
        response = await _http_get(client, str(source.url))
    except httpx.HTTPError as exc:
        log.error("fetch failed for %s: %s", source.id, exc)
        return FetchResult(id=source.id, status="failed", message=str(exc))

    raw = response.content
    content_hash = hashlib.sha256(raw).hexdigest()

    existing = _read_meta(meta_path)
    if (
        existing is not None
        and existing.get("sha256") == content_hash
        and md_path.exists()
    ):
        log.info("unchanged: %s", source.id)
        return FetchResult(
            id=source.id, status="unchanged", sha256=content_hash, path=md_path
        )

    try:
        if _is_pdf_response(source, response):
            markdown = _pdf_to_markdown(raw)
        else:
            markdown = _html_to_markdown(raw)
    except PdfParseError as exc:
        log.error("pdf parse failed for %s: %s", source.id, exc)
        return FetchResult(id=source.id, status="failed", message=str(exc))

    md_path.write_text(markdown, encoding="utf-8")

    meta: dict[str, Any] = {
        "id": source.id,
        "title": source.title,
        "url": str(source.url),
        "format": source.format,
        "licence_url": str(source.licence_url),
        "tenant_id": tenant_id,
        "sha256": content_hash,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    meta_path.write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    log.info("fetched %s (%d bytes -> %s)", source.id, len(raw), md_path)
    return FetchResult(
        id=source.id, status="fetched", sha256=content_hash, path=md_path
    )


def _write_fetched_index(
    cache_dir: Path,
    pairs: list[tuple[Source, str]],
    results: list[FetchResult],
) -> None:
    """Emit ``.fetched.jsonl`` summarising successful fetches for chunk.py.

    Only ``fetched`` and ``unchanged`` rows are included — failed and
    PDF-skipped sources are deliberately invisible to downstream phases.
    """
    index_path = cache_dir / INDEX_FILENAME
    with index_path.open("w", encoding="utf-8") as f:
        for (source, tenant_id), result in zip(pairs, results, strict=True):
            if result.status not in ("fetched", "unchanged"):
                continue
            assert result.sha256 is not None  # invariant of the status filter
            record = FetchedRecord(
                id=source.id,
                tenant_id=tenant_id,
                format=source.format,
                md_path=f"{source.id}.md",
                sha256=result.sha256,
            )
            f.write(record.model_dump_json() + "\n")


async def fetch_all(
    manifest_path: Path,
    cache_dir: Path,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[FetchResult]:
    manifest = load_manifest(manifest_path)
    cache_dir.mkdir(parents=True, exist_ok=True)

    pairs = manifest.resolved()
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(
        transport=transport,
        timeout=HTTP_TIMEOUT,
        limits=HTTP_LIMITS,
        headers=headers,
    ) as client:
        coros = [
            _fetch_one(source, tenant_id, client, cache_dir)
            for source, tenant_id in pairs
        ]
        results = await asyncio.gather(*coros)

    _write_fetched_index(cache_dir, pairs, results)
    return results


_INGEST_DIR = Path(__file__).resolve().parent.parent.parent


def _summarise_and_report(results: list[FetchResult]) -> None:
    """Log the run summary and raise only on systemic failure.

    Pipeline-level resilience: a single dead URL (404 / 5xx-after-retries)
    must not abort ``make ingest``. The downstream ``.fetched.jsonl`` index
    already excludes failed and PDF-skipped sources, so chunk + index
    operate only on what fetched cleanly. We raise only when *every* source
    failed — a systemic issue (auth, DNS, manifest typo) that warrants
    operator attention.
    """
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    log.info("fetch complete: %s", counts)

    failed = [r for r in results if r.status == "failed"]
    succeeded = [r for r in results if r.status in ("fetched", "unchanged")]

    if failed:
        ids = ", ".join(r.id for r in failed)
        log.warning(
            "%d source(s) failed: %s — continuing with %d successful",
            len(failed),
            ids,
            len(succeeded),
        )

    if results and all(r.status == "failed" for r in results):
        raise RuntimeError(f"all {len(results)} sources failed; aborting pipeline")


def run() -> None:
    manifest_path = _INGEST_DIR / "corpus_manifest.yaml"
    cache_dir = _INGEST_DIR / ".cache"

    results = asyncio.run(fetch_all(manifest_path, cache_dir))
    _summarise_and_report(results)
