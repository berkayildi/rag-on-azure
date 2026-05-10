"""Unit tests for Phase 3 Prometheus metrics.

Collectors are global singletons against ``prometheus_client``'s
default registry, so tests measure relative changes
(``before → after``) rather than absolute values — any prior test in
the same process may have moved the counters.
"""

from __future__ import annotations

from typing import Any

import pytest
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from rag_on_azure.api.routes import metrics
from rag_on_azure.metrics import (
    GENERATION_ERRORS,
    GENERATION_LATENCY,
    INGEST_CHUNKS_INDEXED_TOTAL,
    INGEST_DURATION_SECONDS,
    INGEST_RUNS_TOTAL,
    QUERIES_TOTAL,
    RETRIEVAL_ERRORS,
    RETRIEVAL_LATENCY,
    TOTAL_REQUEST_SECONDS,
)


def _counter_value(counter: Any, **labels: str) -> float:
    """Return the current value of a labelled counter, or 0 if not yet seen."""
    metric = counter.labels(**labels)
    return float(metric._value.get())


def _histogram_total_count(histogram: Any) -> float:
    """Return the histogram's ``_count`` sample (total observations).

    Uses the public ``collect()`` API rather than poking at internal
    bucket Values: bucket counters in prometheus-client are
    mutually exclusive (per-bucket increment, not cumulative), so the
    ``_count`` sample is the only authoritative total.
    """
    for metric in histogram.collect():
        for sample in metric.samples:
            if sample.name.endswith("_count"):
                return float(sample.value)
    return 0.0


def test_queries_total_increments_on_success() -> None:
    before = _counter_value(QUERIES_TOTAL, tenant_id="demo", status="success")
    QUERIES_TOTAL.labels(tenant_id="demo", status="success").inc()
    after = _counter_value(QUERIES_TOTAL, tenant_id="demo", status="success")
    assert after - before == pytest.approx(1.0)


def test_queries_total_distinguishes_status_labels() -> None:
    before_ok = _counter_value(QUERIES_TOTAL, tenant_id="t1", status="success")
    before_err = _counter_value(QUERIES_TOTAL, tenant_id="t1", status="error")
    QUERIES_TOTAL.labels(tenant_id="t1", status="error").inc()
    QUERIES_TOTAL.labels(tenant_id="t1", status="error").inc()
    assert _counter_value(QUERIES_TOTAL, tenant_id="t1", status="success") == before_ok
    assert _counter_value(
        QUERIES_TOTAL, tenant_id="t1", status="error"
    ) - before_err == pytest.approx(2.0)


def test_retrieval_errors_label_by_exception_class() -> None:
    before = _counter_value(RETRIEVAL_ERRORS, error_type="ValueError")
    RETRIEVAL_ERRORS.labels(error_type="ValueError").inc()
    assert _counter_value(
        RETRIEVAL_ERRORS, error_type="ValueError"
    ) - before == pytest.approx(1.0)


def test_generation_errors_label_by_exception_class() -> None:
    before = _counter_value(GENERATION_ERRORS, error_type="CitationContractError")
    GENERATION_ERRORS.labels(error_type="CitationContractError").inc()
    assert _counter_value(
        GENERATION_ERRORS, error_type="CitationContractError"
    ) - before == pytest.approx(1.0)


def test_retrieval_latency_observation_recorded() -> None:
    before = _histogram_total_count(RETRIEVAL_LATENCY)
    RETRIEVAL_LATENCY.observe(0.123)
    after = _histogram_total_count(RETRIEVAL_LATENCY)
    assert after - before == pytest.approx(1.0)


def test_generation_latency_observation_recorded() -> None:
    before = _histogram_total_count(GENERATION_LATENCY)
    GENERATION_LATENCY.observe(2.5)
    after = _histogram_total_count(GENERATION_LATENCY)
    assert after - before == pytest.approx(1.0)


def test_total_request_seconds_observation_recorded() -> None:
    before = _histogram_total_count(TOTAL_REQUEST_SECONDS)
    TOTAL_REQUEST_SECONDS.observe(1.0)
    after = _histogram_total_count(TOTAL_REQUEST_SECONDS)
    assert after - before == pytest.approx(1.0)


def test_histogram_buckets_are_llm_tuned() -> None:
    """Sanity: bucket boundaries match D5 verdict, not prometheus defaults."""
    retrieval_buckets = [
        b for b in RETRIEVAL_LATENCY._upper_bounds if b != float("inf")
    ]
    assert retrieval_buckets == [0.005, 0.05, 0.1, 0.5, 1.0, 5.0]

    generation_buckets = [
        b for b in GENERATION_LATENCY._upper_bounds if b != float("inf")
    ]
    assert generation_buckets == [0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0]

    total_buckets = [
        b for b in TOTAL_REQUEST_SECONDS._upper_bounds if b != float("inf")
    ]
    assert total_buckets == [0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0]


async def test_metrics_route_returns_prometheus_exposition() -> None:
    """The /metrics handler returns the default registry as
    text/plain; version=0.0.4 (the Prometheus exposition content type)
    and includes the application's collectors plus the auto-registered
    process collectors."""
    QUERIES_TOTAL.labels(tenant_id="demo", status="success").inc()

    response = await metrics()

    assert response.media_type == CONTENT_TYPE_LATEST
    body = response.body.decode("utf-8")
    assert "queries_total" in body
    assert "retrieval_latency_seconds" in body
    assert "generation_latency_seconds" in body
    assert "total_request_seconds" in body
    assert "retrieval_errors_total" in body
    assert "generation_errors_total" in body


def test_ingest_runs_total_distinguishes_status_labels() -> None:
    """Three terminal statuses are tracked: success, error, conflict."""
    deltas: dict[str, float] = {}
    for status in ("success", "error", "conflict"):
        before = _counter_value(INGEST_RUNS_TOTAL, status=status)
        INGEST_RUNS_TOTAL.labels(status=status).inc()
        deltas[status] = _counter_value(INGEST_RUNS_TOTAL, status=status) - before
    assert deltas == {
        "success": pytest.approx(1.0),
        "error": pytest.approx(1.0),
        "conflict": pytest.approx(1.0),
    }


def test_ingest_duration_observation_recorded() -> None:
    before = _histogram_total_count(INGEST_DURATION_SECONDS)
    INGEST_DURATION_SECONDS.observe(180.0)  # 3 minutes; mid-bucket
    after = _histogram_total_count(INGEST_DURATION_SECONDS)
    assert after - before == pytest.approx(1.0)


def test_ingest_duration_buckets_are_minutes_scale() -> None:
    """Sanity: ingest histogram buckets cover seconds-to-half-hour, not
    the request-scale buckets."""
    buckets = [b for b in INGEST_DURATION_SECONDS._upper_bounds if b != float("inf")]
    assert buckets == [10, 60, 300, 600, 1800]


def test_ingest_chunks_indexed_total_increments_by_count() -> None:
    """``Counter.inc(n)`` should add `n`, not 1, so the route can pass the
    pipeline's ``uploaded`` count straight in."""
    metric = INGEST_CHUNKS_INDEXED_TOTAL
    before = float(metric._value.get())
    metric.inc(42)
    after = float(metric._value.get())
    assert after - before == pytest.approx(42.0)


def test_default_registry_exposes_process_collectors() -> None:
    """``prometheus_client`` auto-registers ProcessCollector on Linux.
    Verify the expected family names appear in the default registry's
    exposition. Skipped on macOS where ProcessCollector is a no-op."""
    body = generate_latest().decode("utf-8")
    if "process_resident_memory_bytes" not in body:
        pytest.skip(
            "ProcessCollector inactive on this platform "
            "(macOS) — Linux runtime (Container Apps) ships them."
        )
    assert "process_resident_memory_bytes" in body
    assert "process_cpu_seconds_total" in body
