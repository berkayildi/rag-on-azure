# AGENTS.md

Operational guidance for AI coding agents working in this repo.
Read this first. Then read `docs/design/rag-on-azure.md` for architecture.

## What this repo is

A reference implementation of a production-grade RAG application on Azure: Bicep IaC, FastAPI + LangGraph application, multi-tenant via JWT-driven filters, CI quality-gated by `mcp-llm-eval`.

## Source of truth

`docs/design/rag-on-azure.md` is the single source of truth for all architecture, structure, naming, and scope decisions. If guidance in this file conflicts with the design spec, the design spec wins. If a request from the user conflicts with the design spec, stop and ask before proceeding.

## Working principles

- Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`, `ci:`)
- One logical change per commit
- Verify each command's output before proceeding to the next step
- Short explanation before non-trivial commands
- All design artefacts as Markdown in `docs/`
- Stop and ask if any instruction conflicts with the design spec

## Build and test

### Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e "app[dev]" -e "ingest[dev]"
pre-commit install
pytest                       # all unit + integration tests
make run                     # uvicorn local server with fakes
```

### Live deployment

```bash
make init                    # azd auth login + az login check
azd env new dev              # one-time per environment
make apply                   # provisions infra (azd provision)
make up                      # Day 5+: provision + deploy image (azd up)
./scripts/seed-corpus.sh     # one-shot ingest
```

### Tear-down

```bash
make down                    # azd down --purge --force, with confirmation countdown
```

## Code conventions

- **Python 3.12**, type-hinted, `mypy --strict` clean
- **Ruff** for lint and format (config in `pyproject.toml`)
- **Pydantic** for all data models — never raw dicts crossing module boundaries
- **Async-first** — all I/O is async; tests use `pytest-asyncio`
- **No LangChain** — LangGraph only (see `docs/design/rag-on-azure.md` §11)
- **No raw API keys in code or env** — managed identity via `DefaultAzureCredential` is the only deployed-stack auth path

## Security non-negotiables

- Personal email addresses, real subscription IDs, and real tenant GUIDs **never** appear in tracked file contents (code, docs, configs) or commit message bodies. Git author metadata is exempt — `user.email` is standard git plumbing.
- Pre-commit `gitleaks` hook must pass before any commit
- `.env*` files are gitignored; only `.env.example` (shape-only, no values) is committed
- Local dev uses `az login` against the user's identity — never a key
- The deployed stack has no keys — managed identity end-to-end
- Anyone forking is told to rotate the JWT signing key in Key Vault before going live

## Versioning

This repo uses **Release Please** (per the `auto-release-bootstrapper` skill) for automated versioning and changelogs. Pre-major v0.x.x lock until v1.0.0. Conventional Commits drive version bumps and CHANGELOG entries. Do not edit `CHANGELOG.md` or version files manually.

## Repo structure

See `docs/design/rag-on-azure.md` §1 for the canonical tree.

Key paths:

- `infra/` — Bicep IaC (entrypoint `infra/main.bicep`)
- `app/` — FastAPI + LangGraph application (Python package `rag_on_azure`)
- `ingest/` — corpus fetch/chunk/index pipeline
- `eval/` — golden dataset + `.eval-gate.yml` thresholds
- `docs/` — architecture, security, deployment narratives
- `scripts/` — operational helpers (corpus seed, JWT minting, secret verification)

## Test layout

- `app/tests/unit/` — pure unit tests, no external dependencies
- `app/tests/integration/` — uses Azurite + a fake Azure AI Search container via Docker Compose
- Two specific tests are audit-grade (see `docs/design/rag-on-azure.md` §5.3):
  - `test_cross_tenant_leak_prevented`
  - `test_missing_tenant_id_raises`

## CI

- `ci.yml` runs lint → unit → integration → secrets scan → build → bicep what-if → deploy dev → eval gate
- `eval-gate.yml` runs nightly against the deployed dev endpoint
- `release-please.yml` handles versioning automation
- Auth to Azure: GitHub OIDC federation. No long-lived service principal secret.

## Stop conditions

Stop and ask the user before proceeding if:

- A skill (`/.claude/skills/...`) gives instructions that contradict the design spec
- Pre-commit hooks fail in a way that requires a config change
- A naming or structural choice isn't clearly resolved by the design spec
- A change would touch `mcp-llm-eval` source (out of scope — see §11)
- A change would touch `llm-benchmarks` source code beyond writing artefact files
- An instruction would put personal data, real secrets, or proprietary info into a file or commit

## Related projects

- `mcp-llm-eval` — evaluation engine, consumed from PyPI at `>=0.7.0,<0.8.0`. Never modified from this repo.
- `llm-benchmarks` — data layer. CI writes `azure-summary.json` and `azure-benchmark.json` to it. No source code changes.
- `llmshot` — visualisation. Consumes `llm-benchmarks` data. No coupling from this repo.
