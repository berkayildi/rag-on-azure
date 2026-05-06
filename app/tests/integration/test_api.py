"""Integration tests for the FastAPI surface.

Uses ``TestClient`` + ``dependency_overrides`` to exercise the full
request → graph → response path with the canonical
``TenantAwareSearchClient`` fronting an in-memory ``FakeSearchClient``
(so the §5.3 audit-grade tenant filter is the real boundary, not
elided), and the ``LLMClient`` Protocol substituted with
``FakeLLMClient`` queued with the responses each test needs.

The TestClient is constructed without entering its context manager so
the FastAPI lifespan does not run — tests don't need (and shouldn't
trigger) real Azure client construction.
"""

from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

from rag_on_azure.auth import resolve_key_vault
from rag_on_azure.settings import get_settings

from rag_on_azure.api.routes import get_graph, get_key_vault, get_llm, get_search
from rag_on_azure.clients.search import TenantAwareSearchClient
from rag_on_azure.graph import build_graph
from rag_on_azure.main import create_app
from rag_on_azure.nodes.generate import Answer
from rag_on_azure.nodes.understand import QueryRewrite

from ..unit.conftest import FakeKeyVaultClient, FakeLLMClient, FakeSearchClient


class _RaisingPing:
    """Pingable test stub whose ``ping()`` raises a chosen exception."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def ping(self) -> None:
        raise self._exc


_DEV_KEY = "dev-only-not-a-secret-padded-32b"


def _mint(claims: dict[str, Any]) -> str:
    return jwt.encode(claims, _DEV_KEY, algorithm="HS256")


def _doc(chunk_id: str, tenant_id: str = "demo") -> dict[str, Any]:
    return {
        "id": chunk_id,
        "tenant_id": tenant_id,
        "source": "fca-cass-7",
        "section_path": "Chapter 1",
        "chunk_text": f"body of {chunk_id}",
        "@search.score": 1.0,
    }


def _wire(
    complete_responses: list[Any],
    docs: list[dict[str, Any]] | None = None,
) -> tuple[TestClient, FakeLLMClient, FakeSearchClient]:
    fake_search = FakeSearchClient(docs=docs or [])
    search_client = TenantAwareSearchClient(inner=fake_search)  # type: ignore[arg-type]
    llm = FakeLLMClient(complete_responses=complete_responses)
    graph = build_graph(llm, search_client)

    app = create_app()
    app.dependency_overrides[get_graph] = lambda: graph
    return TestClient(app), llm, fake_search


def _wire_prod_mode(
    monkeypatch: pytest.MonkeyPatch,
    rsa_keypair: tuple[str, str],
    complete_responses: list[Any] | None = None,
    docs: list[dict[str, Any]] | None = None,
) -> tuple[TestClient, FakeLLMClient, FakeSearchClient, str]:
    """``_wire`` variant that engages production signature verification:
    flips ``ENABLE_DEV_AUTH=false``, busts the settings cache, injects a
    ``FakeKeyVaultClient`` holding the public PEM via the
    ``resolve_key_vault`` dep override. Returns the same triple as
    ``_wire`` plus the matching private PEM so the caller can mint
    RS256-signed tokens that the verifier will accept.
    """
    monkeypatch.setenv("ENABLE_DEV_AUTH", "false")
    get_settings.cache_clear()
    private_pem, public_pem = rsa_keypair
    fake_kv = FakeKeyVaultClient(signing_key=public_pem)

    fake_search = FakeSearchClient(docs=docs or [])
    search_client = TenantAwareSearchClient(inner=fake_search)  # type: ignore[arg-type]
    llm = FakeLLMClient(complete_responses=complete_responses or [])
    graph = build_graph(llm, search_client)

    app = create_app()
    app.dependency_overrides[get_graph] = lambda: graph
    app.dependency_overrides[resolve_key_vault] = lambda: fake_kv
    return TestClient(app), llm, fake_search, private_pem


def _mint_signed(claims: dict[str, Any], private_pem: str) -> str:
    """Mint an RS256 JWT against the supplied private PEM. ``iat`` and
    ``exp`` are auto-populated (60s window) so callers only specify the
    auth-relevant claims (``sub``, ``tenant_id``, optionally ``tenant_admin``).
    """
    now = int(time.time())
    full_claims: dict[str, Any] = {"iat": now, "exp": now + 60, **claims}
    return jwt.encode(full_claims, private_pem, algorithm="RS256")


def test_healthz_returns_200() -> None:
    app = create_app()
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_all_ok() -> None:
    """All three checks succeed: real ``TenantAwareSearchClient.ping()``
    reaches ``FakeSearchClient.get_document_count``; ``FakeLLMClient.ping``
    and ``FakeKeyVaultClient.ping`` record the calls. Returns 200."""
    fake_llm = FakeLLMClient()
    fake_search_inner = FakeSearchClient(docs=[])
    search_client = TenantAwareSearchClient(inner=fake_search_inner)  # type: ignore[arg-type]
    fake_kv = FakeKeyVaultClient()

    app = create_app()
    app.dependency_overrides[get_llm] = lambda: fake_llm
    app.dependency_overrides[get_search] = lambda: search_client
    app.dependency_overrides[get_key_vault] = lambda: fake_kv
    client = TestClient(app)

    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"openai": "ok", "search": "ok", "key_vault": "ok"},
    }
    assert fake_llm.ping_calls == 1
    assert fake_search_inner.get_document_count_calls == 1
    assert fake_kv.ping_calls == 1


def test_readyz_partial_failure() -> None:
    """One check fails → 503 + ``not_ready``; the failed check carries the
    raised exception's type name; healthy checks still report ``ok``."""
    app = create_app()
    app.dependency_overrides[get_llm] = lambda: FakeLLMClient()
    app.dependency_overrides[get_search] = lambda: _RaisingPing(
        RuntimeError("search down")
    )
    app.dependency_overrides[get_key_vault] = lambda: FakeKeyVaultClient()
    client = TestClient(app)

    response = client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["openai"] == "ok"
    assert body["checks"]["search"] == "failed: RuntimeError"
    assert body["checks"]["key_vault"] == "ok"


def test_readyz_key_vault_failure_only() -> None:
    """Key Vault check failing alone is enough to fail readiness — the
    auth path needs KV, so a degraded KV degrades the whole verifier."""
    fake_search_inner = FakeSearchClient(docs=[])
    search_client = TenantAwareSearchClient(inner=fake_search_inner)  # type: ignore[arg-type]

    app = create_app()
    app.dependency_overrides[get_llm] = lambda: FakeLLMClient()
    app.dependency_overrides[get_search] = lambda: search_client
    app.dependency_overrides[get_key_vault] = lambda: FakeKeyVaultClient(
        ping_raises=ConnectionError("kv down")
    )
    client = TestClient(app)

    response = client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["openai"] == "ok"
    assert body["checks"]["search"] == "ok"
    assert body["checks"]["key_vault"] == "failed: ConnectionError"


def test_readyz_full_failure() -> None:
    """All three checks raise → 503 with each marked failed by type name."""
    app = create_app()
    app.dependency_overrides[get_llm] = lambda: _RaisingPing(
        ConnectionError("openai down")
    )
    app.dependency_overrides[get_search] = lambda: _RaisingPing(
        RuntimeError("search down")
    )
    app.dependency_overrides[get_key_vault] = lambda: _RaisingPing(
        TimeoutError("kv timed out")
    )
    client = TestClient(app)

    response = client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["openai"] == "failed: ConnectionError"
    assert body["checks"]["search"] == "failed: RuntimeError"
    assert body["checks"]["key_vault"] == "failed: TimeoutError"


def test_query_requires_auth() -> None:
    client, _, _ = _wire(complete_responses=[], docs=[])
    response = client.post("/query", json={"question": "x"})
    assert response.status_code == 401


def test_query_returns_rag_response_with_citations() -> None:
    client, _, _ = _wire(
        complete_responses=[
            QueryRewrite(rewritten_query="Client Assets Sourcebook 7"),
            Answer(text="CASS 7 requires...", cited_chunk_ids=["c1"]),
        ],
        docs=[_doc("c1")],
    )
    token = _mint({"sub": "u", "tenant_id": "demo"})

    response = client.post(
        "/query",
        json={"question": "What does CASS 7 require?"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "CASS 7 requires..."
    assert len(body["citations"]) == 1
    assert body["citations"][0]["chunk_id"] == "c1"
    assert body["citations"][0]["source"] == "fca-cass-7"


def test_query_propagates_tenant_id_from_jwt_into_search_filter() -> None:
    """End-to-end §5.3 evidence at the API boundary: tenant_id flows
    JWT → TenantContext → GraphState → search filter, never from body."""
    client, _, fake_search = _wire(
        complete_responses=[
            QueryRewrite(rewritten_query="x"),
            Answer(text="answer", cited_chunk_ids=[]),
        ],
        docs=[_doc("c1", tenant_id="acme")],
    )
    token = _mint({"sub": "u", "tenant_id": "acme"})

    response = client.post(
        "/query",
        json={"question": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert fake_search.search_calls[0]["filter"].startswith("tenant_id eq 'acme'")


def test_query_502_on_citation_contract_error() -> None:
    """Two consecutive fabricated-citation responses (one initial + one
    retry) trip CitationContractError; the API returns 502."""
    client, _, _ = _wire(
        complete_responses=[
            QueryRewrite(rewritten_query="x"),
            Answer(text="bad1", cited_chunk_ids=["c-fake-1"]),
            Answer(text="bad2", cited_chunk_ids=["c-fake-2"]),
        ],
        docs=[_doc("c1")],
    )
    token = _mint({"sub": "u", "tenant_id": "demo"})

    response = client.post(
        "/query",
        json={"question": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "upstream model failed citation contract"


def test_query_400_on_invalid_tenant_id_in_jwt() -> None:
    """A JWT carrying a tenant_id that fails the search client's
    ``^[a-z0-9-]+$`` validation (e.g. a space) is a 400 — the request
    is malformed at the auth boundary even though the JWT itself
    decoded cleanly."""
    client, _, _ = _wire(
        complete_responses=[QueryRewrite(rewritten_query="x")],
        docs=[],
    )
    token = _mint({"sub": "u", "tenant_id": "Tenant Acme"})

    response = client.post(
        "/query",
        json={"question": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# /ingest — admin-only route (Phase 3)
# ---------------------------------------------------------------------------


def test_ingest_requires_auth() -> None:
    """No Authorization header → 401 from the auth boundary, before the
    admin gate or the 501 stub is reached."""
    app = create_app()
    client = TestClient(app)
    response = client.post("/ingest")
    assert response.status_code == 401


def test_ingest_requires_admin_claim() -> None:
    """A valid token without ``tenant_admin`` is rejected by the admin
    gate with 403; the 501 stub is never reached."""
    app = create_app()
    client = TestClient(app)
    token = _mint({"sub": "u", "tenant_id": "demo"})  # no tenant_admin
    response = client.post("/ingest", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert response.json()["detail"] == "admin claim required"


def test_ingest_admin_token_passes_gate_returns_501() -> None:
    """Admin token passes the gate; the route itself returns 501 — the
    auth gate is the Day 6 deliverable, pipeline lands Day 7."""
    app = create_app()
    client = TestClient(app)
    token = _mint({"sub": "u", "tenant_id": "demo", "tenant_admin": True})
    response = client.post("/ingest", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 501
    detail = response.json()["detail"]
    assert "not yet wired" in detail
    assert "Day 7" in detail


def test_ingest_rejects_unsigned_when_dev_auth_off(
    monkeypatch: pytest.MonkeyPatch, rsa_keypair: tuple[str, str]
) -> None:
    """End-to-end proof that the admin gate composes correctly with
    Phase 2 verification: under ``ENABLE_DEV_AUTH=false``, an unsigned
    token carrying ``tenant_admin=True`` is still rejected at the
    signature-verification layer (401) — admin claims do not bypass
    the verifier."""
    monkeypatch.setenv("ENABLE_DEV_AUTH", "false")
    get_settings.cache_clear()

    _, public_pem = rsa_keypair
    fake_kv = FakeKeyVaultClient(signing_key=public_pem)

    app = create_app()
    app.dependency_overrides[resolve_key_vault] = lambda: fake_kv
    client = TestClient(app)

    now = int(time.time())
    unsigned = jwt.encode(
        {
            "sub": "u",
            "tenant_id": "demo",
            "tenant_admin": True,
            "iat": now,
            "exp": now + 60,
        },
        key="",
        algorithm="none",
    )
    response = client.post("/ingest", headers={"Authorization": f"Bearer {unsigned}"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Prod-mode (ENABLE_DEV_AUTH=false) end-to-end coverage — Phase 4
# ---------------------------------------------------------------------------
#
# These tests exercise the full chain with signature verification engaged:
# signed RS256 token → verifier fetches public PEM from the fake KV →
# decoded claims drive TenantContext → graph state → search filter (or,
# for /ingest, the admin gate). They are the integration-level proof
# that Phases 1-3 compose correctly under production semantics.


def test_query_under_prod_mode_propagates_tenant_id_to_search_filter(
    monkeypatch: pytest.MonkeyPatch, rsa_keypair: tuple[str, str]
) -> None:
    """The §5.3 audit invariant under prod-mode: tenant_id flows
    JWT → TenantContext → GraphState → search filter, with full RS256
    signature verification engaged above the boundary."""
    client, _, fake_search, private_pem = _wire_prod_mode(
        monkeypatch,
        rsa_keypair,
        complete_responses=[
            QueryRewrite(rewritten_query="x"),
            Answer(text="answer", cited_chunk_ids=[]),
        ],
        docs=[_doc("c1", tenant_id="demo")],
    )
    token = _mint_signed({"sub": "u", "tenant_id": "demo"}, private_pem)

    response = client.post(
        "/query",
        json={"question": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert fake_search.search_calls[0]["filter"].startswith("tenant_id eq 'demo'")


def test_query_under_prod_mode_cross_tenant_attempt_isolated(
    monkeypatch: pytest.MonkeyPatch, rsa_keypair: tuple[str, str]
) -> None:
    """Two chunks share the fake index — one for tenant 'demo', one for
    tenant 'other'. A request authenticated as 'demo' must only see and
    cite the demo chunk. Mirrors the unit-level audit-grade
    ``test_cross_tenant_leak_prevented`` at the API edge under prod-mode
    signature verification."""
    client, _, fake_search, private_pem = _wire_prod_mode(
        monkeypatch,
        rsa_keypair,
        complete_responses=[
            QueryRewrite(rewritten_query="x"),
            Answer(text="demo content", cited_chunk_ids=["demo-chunk"]),
        ],
        docs=[
            _doc("demo-chunk", tenant_id="demo"),
            _doc("other-chunk", tenant_id="other"),
        ],
    )
    token = _mint_signed({"sub": "u", "tenant_id": "demo"}, private_pem)

    response = client.post(
        "/query",
        json={"question": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    # Boundary evidence: the OData filter pinned tenant_id to demo.
    assert fake_search.search_calls[0]["filter"].startswith("tenant_id eq 'demo'")
    # Surface evidence: no other-tenant chunk leaked through citations.
    citation_ids = {c["chunk_id"] for c in response.json()["citations"]}
    assert citation_ids == {"demo-chunk"}


def test_ingest_admin_token_under_prod_mode_returns_501(
    monkeypatch: pytest.MonkeyPatch, rsa_keypair: tuple[str, str]
) -> None:
    """Full chain under prod-mode for the admin route: signed token →
    signature verified against KV-fetched public PEM → tenant_admin
    claim accepted by ``get_current_admin`` → route reaches its 501
    stub. No layer is short-circuited."""
    client, _, _, private_pem = _wire_prod_mode(monkeypatch, rsa_keypair)
    token = _mint_signed(
        {"sub": "u", "tenant_id": "demo", "tenant_admin": True},
        private_pem,
    )

    response = client.post(
        "/ingest",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 501
