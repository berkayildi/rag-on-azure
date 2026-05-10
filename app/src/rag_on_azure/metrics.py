"""Prometheus collectors for the rag-on-azure FastAPI app.

See ``docs/design/rag-on-azure.md`` §3.4.

Collectors are module-level singletons against ``prometheus_client``'s
default registry. The ``/metrics`` route exposes them via
``generate_latest()``. ``prometheus_client`` auto-registers
``ProcessCollector``, ``PlatformCollector``, and ``GCCollector`` on
import, so process-level metrics (memory, CPU, fds, GC counts) ship
without explicit setup on Linux runtime (Container Apps).

Cardinality notes:

- ``queries_total{tenant_id, status}`` — tenant_id grows linearly with
  tenant count. Demo has one tenant. Revisit at >100 tenants;
  Prometheus best practice keeps single-label cardinality bounded.
- ``retrieval_errors_total{error_type}`` and
  ``generation_errors_total{error_type}`` — error_type is the
  exception class name. Bounded in practice (Python type system).
  No tenant label: error counters intentionally aggregate across
  tenants so a single misbehaving tenant does not bloat cardinality.
- Histogram buckets are tuned for LLM-shaped latencies, not the
  prometheus-client defaults (which target HTTP).
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

QUERIES_TOTAL = Counter(
    "queries_total",
    "Total /query requests received, labelled by tenant and outcome.",
    labelnames=("tenant_id", "status"),
)

RETRIEVAL_ERRORS = Counter(
    "retrieval_errors_total",
    "Errors raised inside the retrieve graph node, by exception class.",
    labelnames=("error_type",),
)

GENERATION_ERRORS = Counter(
    "generation_errors_total",
    "Errors raised inside the generate graph node, by exception class.",
    labelnames=("error_type",),
)

RETRIEVAL_LATENCY = Histogram(
    "retrieval_latency_seconds",
    "Retrieve node wall-clock latency (embed + hybrid search).",
    buckets=(0.005, 0.05, 0.1, 0.5, 1.0, 5.0),
)

GENERATION_LATENCY = Histogram(
    "generation_latency_seconds",
    "Generate node wall-clock latency (LLM call + citation validation).",
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
)

TOTAL_REQUEST_SECONDS = Histogram(
    "total_request_seconds",
    "End-to-end /query handler wall-clock latency.",
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
)

INGEST_RUNS_TOTAL = Counter(
    "ingest_runs_total",
    "Total /ingest runs, labelled by terminal outcome.",
    labelnames=("status",),
)

INGEST_DURATION_SECONDS = Histogram(
    "ingest_duration_seconds",
    "Wall-clock duration of an /ingest run, end to end.",
    # Ingest is minutes-scale (fetch + chunk + embed + upload); buckets
    # tuned for that band, not the request-scale buckets above.
    buckets=(10, 60, 300, 600, 1800),
)

INGEST_CHUNKS_INDEXED_TOTAL = Counter(
    "ingest_chunks_indexed_total",
    "Total chunks uploaded to the search index across all /ingest runs.",
)
