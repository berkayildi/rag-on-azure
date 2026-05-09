# rag-on-azure

A reference implementation of a production-grade RAG (Retrieval-Augmented Generation) application on Microsoft Azure: Bicep IaC, FastAPI + LangGraph, multi-tenant via JWT-driven filters, CI quality-gated by `mcp-llm-eval`. Intended to be forked or used as architectural reference for teams building RAG on the Azure stack.

> **Status: under construction.** The repository is currently being scaffolded — directory structure, tooling, and CI plumbing are landing first; deployable Bicep and application code follow in subsequent commits.

## Source of truth

The canonical architecture, scope, naming, and security model live in [`docs/design/rag-on-azure.md`](docs/design/rag-on-azure.md). That document is the single source of truth for every decision in this repo.

## Development

Day-to-day operations are wrapped in a `Makefile` (Terraform-shaped ergonomics over the underlying `azd` + Bicep flow):

```bash
make plan      # preview infra changes (az deployment group what-if)
make apply     # provision infra (azd provision)
make outputs   # print FQDN, endpoints, env vars
make down      # tear down (azd down --purge --force, with countdown)
```

Run `make help` for the full target list, or read [`Makefile`](Makefile) directly.

For local development, copy [`.env.example`](.env.example) to `.env` and fill in real endpoints. See [`AGENTS.md`](AGENTS.md) for the auth-toggle behaviour and the one-time RSA keypair setup used by `scripts/mint-token.py`.

## API surface

- `POST /query` — body `{question, top_k?}`; bearer JWT required; returns the grounded answer with citations.
- `GET /healthz` — liveness probe; auth-free.
- `GET /readyz` — readiness probe; pings each runtime client; returns 503 if any check fails.
- `GET /metrics` — Prometheus exposition (counters: `queries_total`, `retrieval_errors_total`, `generation_errors_total`; histograms: `retrieval_latency_seconds`, `generation_latency_seconds`, `total_request_seconds`; plus the standard `process_*`/`python_*` collectors). **Public in the demo posture** — production should gate via network allowlist or admin-JWT bearer.
- `POST /ingest` — admin-only; gated on the `tenant_admin` JWT claim. Currently returns 501 (Day 7 wires the pipeline).

Full surface specification in [`docs/design/rag-on-azure.md`](docs/design/rag-on-azure.md) §3.4.

## Licence

Released under the [MIT Licence](LICENSE).
