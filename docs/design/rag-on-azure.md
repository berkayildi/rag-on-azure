# rag-on-azure — Design Specification

**Status:** Draft v1.2
**Author:** Berkay Yildirim
**Last updated:** 27 April 2026
**mcp-llm-eval consumed version:** `>=0.7.0,<0.8.0`

A reference implementation of a production-grade RAG (Retrieval-Augmented Generation) application on Microsoft Azure. The repo is intended to be forked or used as architectural reference for teams building RAG on the Azure stack.

## 0. Pre-deployment checklist

Before any code is written, the Azure subscription must be in a usable state.

- [ ] Active Azure subscription with payment method on file
- [ ] Confirm Azure OpenAI access is enabled on the subscription. New subscriptions historically required an access request form; verify via portal whether the current subscription needs one and submit if so.
- [ ] Pick the deployment region. **Sweden Central** is the default for this repo. As of April 2026, new Azure subscriptions had zero default OpenAI quota in UK South (the original target), and `text-embedding-3-small` was not offered on regional `Standard` SKU in Sweden Central either. Additionally, as of 28 April 2026 `gpt-4o-mini@2024-07-18` is no longer accepting new deployments anywhere; Microsoft's recommended replacement (`gpt-4.1-mini@2025-04-14`) has zero default quota in Sweden Central on new subscriptions. The deployable alternative is `gpt-4o@2024-08-06` on regional `Standard` (50 TPM quota, sunset April 2027). The deployment therefore uses mixed SKUs to stay inside the EU: `gpt-4o` on regional `Standard` (chat inference fully within Sweden Central), `text-embedding-3-small` on `DataZoneStandard` (embedding inference within the EU data zone). Quality of `gpt-4o` is strictly better than the originally specified `gpt-4o-mini`, so the eval-gate thresholds (§6.3) only get easier; cost is ~30× per token but absolute build-phase spend stays inside the £25 budget (see §9.1). UK South with `gpt-4o-mini` remains the long-term target if quota and SKU availability return there. Confirm regional availability **and** quota of all three model+service combinations before committing infra.

  Within the `gpt-4o` family, `@2024-08-06` was the natural pick (newest with renewed sunset) but Azure's deploy validator rejects it because it carries a dual SKU listing — one entry expired on 2026-03-31, one renewed to 2027-03-31, and the validator hits the older one. The deployable variant is **`gpt-4o@2024-11-20`** on regional `Standard`: single SKU listing, deployable until 2026-10-01 (~5 months at time of first deploy), 50 TPM quota in the same `OpenAI.Standard.gpt-4o` bucket. Microsoft has already begun auto-migrating `@2024-11-20` deployments to `gpt-4.1` under the same deployment endpoint (`autoUpgradeStartDate: 2026-02-14`), so the live deployment will transparently upgrade to `gpt-4.1` over the coming weeks without any action on our part — net effect, the stack ends up running `gpt-4.1` past the nominal 2026-10-01 sunset, with the same endpoint and managed-identity wiring.
- [ ] Install local toolchain: `az` CLI ≥ 2.60, `azd` (Azure Developer CLI) ≥ 1.10, Bicep ≥ 0.27, Python 3.12, Docker, `gitleaks`.
- [ ] `az login` — confirm Azure CLI is authenticated against the right tenant.
- [ ] Run `az deployment group what-if` against an empty resource group to verify the auth path before writing real Bicep.

**Fallback if Azure OpenAI quota is delayed:** the `LLMClient` adapter (see §3) accepts an OpenAI direct client during local dev. Initial development can proceed against OpenAI direct; the swap to Azure OpenAI is a single config change once quota lands.

## 1. Repository structure

```
rag-on-azure/
├── .github/
│   └── workflows/
│       ├── ci.yml                    # primary CI
│       ├── eval-gate.yml             # nightly eval against deployed dev
│       └── release-please.yml        # Release Please automation
├── azure-pipelines.yml               # Azure Pipelines mirror
├── release-please-config.json
├── .release-please-manifest.json
├── infra/
│   ├── main.bicep
│   ├── modules/
│   │   ├── search.bicep
│   │   ├── openai.bicep
│   │   ├── containerapp.bicep
│   │   ├── keyvault.bicep
│   │   └── monitor.bicep
│   ├── main.parameters.json          # gitignored real values
│   └── main.parameters.example.json  # committed shape only
├── app/
│   ├── pyproject.toml
│   ├── src/rag_on_azure/
│   │   ├── __init__.py
│   │   ├── api.py
│   │   ├── auth.py
│   │   ├── graph.py
│   │   ├── nodes/
│   │   │   ├── understand.py
│   │   │   ├── retrieve.py
│   │   │   └── generate.py
│   │   ├── clients/
│   │   │   ├── llm.py
│   │   │   └── search.py
│   │   ├── models.py
│   │   └── settings.py
│   ├── tests/
│   │   ├── unit/
│   │   ├── integration/
│   │   └── conftest.py
│   └── Dockerfile
├── ingest/
│   ├── pyproject.toml
│   ├── src/ingest/
│   │   ├── fetch.py
│   │   ├── chunk.py
│   │   └── index.py
│   └── corpus_manifest.yaml
├── eval/
│   ├── golden.jsonl
│   └── .eval-gate.yml
├── docs/
│   ├── design/
│   │   └── rag-on-azure.md
│   ├── architecture.md
│   ├── security.md
│   └── deployment.md
├── scripts/
│   ├── verify-no-secrets.sh
│   └── seed-corpus.sh
├── .gitignore
├── .gitleaks.toml
├── .pre-commit-config.yaml
├── azure.yaml
├── LICENSE
├── README.md
└── CHANGELOG.md
```

**Naming convention:** generic OSS-conventional, no `azure-` prefix on filenames. The Azure-ness is visible inside the files.

## 2. Bicep module breakdown

### 2.1 `main.bicep`

- Resource group scope, parameter inputs: `location`, `environmentName` (`dev` | `prod`), `tenantSeedIds` (string array)
- `location` defaults to `swedencentral` (see §0); override per environment via parameters file
- Outputs: `containerAppFqdn`, `searchEndpoint`, `openaiEndpoint`, `keyVaultName`
- Wires modules in order: monitor → keyvault → search → openai → containerapp

### 2.2 `modules/search.bicep`

- SKU: `free` for dev, `basic` for production-grade workloads (gated by parameter)
- Single search service, single index named `corpus`
- Index schema: `id` (key), `tenant_id` (filterable, facetable), `source` (filterable), `chunk_text` (searchable), `chunk_vector` (1536-dim, HNSW)
- Hybrid retrieval: BM25 + vector with reciprocal rank fusion at query time
- Output: `searchEndpoint`, `searchName`

### 2.3 `modules/openai.bicep`

- Cognitive Services account, kind `OpenAI`, SKU `S0`
- Two model deployments (mixed deployment SKUs forced by Sweden Central availability — see §0; the split is not a design preference):
  - `embedding`: `text-embedding-3-small@1`, deployment SKU `DataZoneStandard`, capacity 30 (30k TPM)
  - `chat`: `gpt-4o@2024-11-20`, deployment SKU `Standard`, capacity 50 (50k TPM) — substituted for `gpt-4o-mini` because that model stopped accepting new deployments on 2026-03-31; pinned to `@2024-11-20` (not `@2024-08-06`) because the latter is rejected by the validator (see §0)
- Public network access enabled for dev; documented as private endpoint in prod
- Output: `openaiEndpoint`, deployment names

### 2.4 `modules/containerapp.bicep`

- Container Apps Environment with Log Analytics workspace bound
- Container App with:
  - System-assigned managed identity
  - Image from public GHCR (built in CI; for first deploy a placeholder)
  - Env vars: `AZURE_SEARCH_ENDPOINT`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_CHAT_DEPLOYMENT`, `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`, `KEY_VAULT_URI`, `JWT_SIGNING_KEY_REF`
  - Min replicas 0, max 3, scale rule on HTTP concurrency
  - Ingress: external, port 8000
- Role assignments to its managed identity:
  - `Cognitive Services OpenAI User` on the OpenAI account
  - `Search Index Data Reader` + `Search Index Data Contributor` on the Search service
  - `Key Vault Secrets User` on Key Vault
- Output: `containerAppFqdn`

### 2.5 `modules/keyvault.bicep`

- Key Vault, RBAC-enabled (no access policies)
- One secret: `jwt-signing-key` (placeholder; real value set out-of-band)
- Output: `keyVaultUri`

### 2.6 `modules/monitor.bicep`

- Log Analytics workspace
- Application Insights, classic mode, connected to the workspace
- Output: workspace ID, App Insights connection string

## 3. Application — LangGraph + FastAPI

### 3.1 State schema (Pydantic)

```python
class GraphState(BaseModel):
    question: str
    tenant_id: str                          # injected from JWT, never from input
    rewritten_query: str | None = None
    filters: dict[str, str] = {}
    retrieved_chunks: list[Chunk] = []
    answer: str | None = None
    citations: list[Citation] = []
    metadata: dict = {}
```

### 3.2 Nodes

**`understand`** — input: `GraphState`. Calls `LLMClient` to rewrite the user question for retrieval (expands acronyms, extracts year/jurisdiction filters). Output: populates `rewritten_query` and `filters`.

**`retrieve`** — input: `GraphState`. Calls `TenantAwareSearchClient.hybrid_search(query, tenant_id, filters, top_k=5)`. The tenant_id parameter is **non-optional**; the client signature makes it impossible to call without one. Output: populates `retrieved_chunks`.

**`generate`** — input: `GraphState`. Calls `LLMClient` with structured output schema (Pydantic `Answer` model with `text: str` and `cited_chunk_ids: list[str]`). Validates that every cited ID exists in `retrieved_chunks`; rejects answer if not (re-run with stricter prompt, max 1 retry). Output: populates `answer` and `citations`.

Graph is linear: `understand → retrieve → generate → END`.

### 3.3 Adapters

**`LLMClient` Protocol:**

```python
class LLMClient(Protocol):
    async def complete(self, messages: list[Message], schema: type[BaseModel] | None = None) -> Any: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
```

Two implementations:

- `AzureOpenAIClient` — uses `DefaultAzureCredential`, no key
- `OpenAIDirectClient` — used during local dev only when explicitly opted into via env var, behind a warning log

**`TenantAwareSearchClient`:**

```python
class TenantAwareSearchClient:
    def __init__(self, endpoint: str, index_name: str, credential): ...
    async def hybrid_search(
        self, query: str, tenant_id: str, filters: dict[str, str] | None = None, top_k: int = 5
    ) -> list[Chunk]: ...
```

`tenant_id` is injected as `tenant_id eq '{tenant_id}'` in the OData filter, ANDed with any user-supplied filters. There is no `search()` method that omits tenant — by construction.

### 3.4 FastAPI surface

- `POST /query` — body: `{question, top_k?}`; auth: bearer JWT; returns `Answer` + citations + metadata
- `GET /healthz` — liveness, returns 200 if graph constructed and clients reachable
- `GET /readyz` — readiness, hits dependencies once
- `GET /metrics` — Prometheus format
- `POST /ingest` — admin-only, behind `tenant_admin` JWT claim

### 3.5 Auth flow

JWT signed with the Key Vault key. Required claims: `sub`, `tenant_id`, `exp`, `iat`. `auth.py` validates signature against the public key (cached 5 min), extracts `tenant_id`, attaches to request state. `tenant_id` flows from request state into `GraphState`. **No path exists where `tenant_id` comes from request body or query string.**

Local dev convenience: a `scripts/mint-token.py` generates a tenant JWT against a local dev key for curl-based testing. Documented in README as dev-only.

## 4. Corpus and chunking

### 4.1 Sources (public, regulatory, redistributable)

The reference corpus uses publicly available UK regulatory documents:

- **FCA Handbook** — selected modules: COND, SYSC, CASS 7, CASS 9, FCG (Financial Crime Guide). Pulled from `handbook.fca.org.uk`. Approx 30 documents.
- **HMRC guidance** — Trust Registration Service guidance, AML for trust services, beneficial ownership reporting. Pulled from `gov.uk`. Approx 10 documents.
- **FATF recommendations** — current 40 Recommendations, Recommendation 24/25 interpretive notes. Approx 5 documents.

Total: ~45 source documents, ~600 chunks after splitting. Forks are encouraged to swap in their own corpus.

### 4.2 Manifest

`ingest/corpus_manifest.yaml` records each source's URL, fetch date, content hash, licence URL, and a per-document `tenant_id`. The default seed loads everything into a `demo` tenant; integration tests use `demo-a` and `demo-b` to prove isolation.

### 4.3 Chunking

Markdown-aware splitter (langchain `MarkdownHeaderTextSplitter` then recursive `RecursiveCharacterTextSplitter`):

- Chunk size: 800 tokens
- Overlap: 100 tokens
- Headings preserved as metadata (`section_path`)
- Each chunk gets: `id` (deterministic hash of source+offset), `tenant_id`, `source`, `section_path`, `chunk_text`, `chunk_vector`

### 4.4 Embedding

Batched calls to `text-embedding-3-small` via `LLMClient.embed`. Idempotent — re-running ingest skips chunks whose hash is unchanged.

## 5. Tenant isolation

### 5.1 Pattern

Shared index, JWT-driven tenant filter, enforced at the `TenantAwareSearchClient` boundary.

### 5.2 Why not index-per-tenant

Documented in `docs/architecture.md`. Index-per-tenant doesn't scale past ~3 tenants on Free/Basic, requires per-tenant provisioning, and is rare in production multi-tenant SaaS. Filter-based isolation is the dominant pattern (Microsoft's own RAG samples use it). Index-per-tenant is positioned as the **v2 escalation** for high-value tenants requiring physical separation.

### 5.3 Proof

Two integration tests:

- `test_cross_tenant_leak_prevented` — tenant A indexes a unique sentinel doc; tenant B queries for it; result must be empty
- `test_missing_tenant_id_raises` — calling the search client without a `tenant_id` argument is a `TypeError` at construction (proven by mypy in CI as well as runtime)

These two tests are the audit-grade evidence that the pattern works.

## 6. CI/CD

### 6.1 Primary: GitHub Actions (`.github/workflows/ci.yml`)

Triggers: push to main, all PRs.

Stages:

1. **Lint** — `ruff`, `mypy --strict`, `bicep build` (validates IaC)
2. **Test** — `pytest app/tests/unit` (no external dependencies)
3. **Integration test** — spin up Azurite + a fake Azure AI Search container; run `pytest app/tests/integration`
4. **Secrets scan** — `gitleaks detect --source . --redact`
5. **Build** — Docker image, push to GHCR with `sha-` and `latest-dev` tags
6. **Bicep what-if** — against the dev resource group (read-only diff)
7. **Deploy dev** — `azd deploy --no-prompt` to `dev` environment (only on `main`)
8. **Eval gate** — `mcp-llm-eval evaluate-rag --endpoint $DEV_FQDN --dataset eval/golden.jsonl --gate eval/.eval-gate.yml`. Gate failure blocks the run; results posted as PR comment.

Auth: GitHub OIDC federation to Azure AD. No long-lived service principal secret in repo settings.

### 6.2 Mirror: Azure Pipelines (`azure-pipelines.yml`)

Same stages, Azure Pipelines idioms (`stages → jobs → steps`, `AzureCLI@2` task, `azd` install step). Provided for users running this stack inside Azure DevOps environments. Wired against a separate Azure DevOps org if available; otherwise the YAML stands alone as a reference.

### 6.3 Eval gate config (`eval/.eval-gate.yml`)

```yaml
schema_version: '0.7'
retrieval:
  recall_at_5:
    min: 0.75
  precision_at_5:
    min: 0.40
  mrr:
    min: 0.65
rag:
  citation_faithfulness:
    min: 0.85
  context_relevance:
    min: 0.75
latency:
  p95_ms:
    max: 3500
on_regression:
  action: fail
  delta_threshold: 0.05
```

Thresholds set conservatively for v1; tightened as the corpus stabilises.

## 7. Security model

### 7.1 Threats considered

| Threat                                        | Mitigation                                                                                                 |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| API key leaked in repo                        | Managed identity end-to-end; gitleaks pre-commit + push protection; `.env*` gitignored                     |
| Long-lived CI secret leaked                   | OIDC federation; no service principal secret stored                                                        |
| Cross-tenant data leak                        | `TenantAwareSearchClient` makes filter omission a type error; integration test proves isolation            |
| Prompt injection extracts other tenants' data | Tenant filter applied at retrieval, before LLM ever sees chunks; LLM cannot retrieve what it cannot see    |
| Citation hallucination                        | `generate` node validates every cited ID exists in retrieved set; rejects if not                           |
| Excessive token spend                         | TPM quota on the model deployment is the hard ceiling; per-tenant request rate limit at FastAPI middleware |
| Denial-of-wallet                              | Container App max replicas = 3; budget alert at £40                                                        |

### 7.2 What the README is explicit about

- No OpenAI direct keys are required or supported in deployed configurations
- `.env.example` shows shape only; never real values
- Local dev uses `az login` against your user identity, never a key
- The deployed stack has no keys — managed identity only
- Anyone forking is told to rotate the JWT signing key in Key Vault before going live

### 7.3 Tooling

- `gitleaks` pre-commit hook + CI step
- GitHub repo settings: secret scanning **on**, push protection **on**, Dependabot **on**
- `pip-audit` weekly scheduled run

## 8. Local development story

### 8.1 First-run

```bash
git clone git@github.com:berkayildi/rag-on-azure.git
cd rag-on-azure
pre-commit install
python -m venv .venv && source .venv/bin/activate
pip install -e "app[dev]" -e "ingest[dev]"
cp .env.example .env       # fill in nothing; defaults work for local
docker compose up -d       # azurite + a search emulator container
pytest                     # all unit + integration tests pass
make run                   # uvicorn against fakes; curl localhost:8000
```

### 8.2 Against real Azure

```bash
az login
azd auth login
azd env new dev
azd up                     # provisions everything, deploys image, returns URL
./scripts/seed-corpus.sh   # one-shot ingest of FCA/HMRC/FATF
curl -H "Authorization: Bearer $(python scripts/mint-token.py demo)" \
     -d '{"question":"What does CASS 7 require?"}' \
     "https://${AZD_FQDN}/query"
```

### 8.3 Tear-down

```bash
azd down --purge --force-delete      # nukes the resource group cleanly
```

## 9. Cost envelope

### 9.1 Initial deployment

| Component                    | Tier                                                                | Estimated cost           |
| ---------------------------- | ------------------------------------------------------------------- | ------------------------ |
| Azure AI Search              | Free                                                                | £0                       |
| Azure OpenAI                 | S0, gpt-4o + text-embedding-3-small, ~50 eval runs × 20 q × 1.5k tk | ~£10                     |
| Container Apps               | Consumption, scale-to-zero                                          | <£1 (within free grants) |
| App Insights + Log Analytics | First 5GB free                                                      | ~£1                      |
| Key Vault                    | Standard                                                            | <£0.10                   |
| **Initial total**            |                                                                     | **~£12**                 |

The OpenAI line lifted from ~£3 (the originally specified `gpt-4o-mini`) to ~£10 because `gpt-4o-mini` was no longer deployable on new subscriptions when this stack first deployed (see §0); `gpt-4o` is ~30× per token but the absolute build-phase total stays inside the £25 budget. If `gpt-4o-mini` (or `gpt-4.1-mini`) becomes deployable again with non-zero quota, swap back via `infra/main.parameters.json` and the OpenAI line returns to ~£3.

### 9.2 Steady-state, low traffic

Endpoint stays up, ~50 queries/day, scale-to-zero between bursts.

| Component       | Cost/month    |
| --------------- | ------------- |
| Azure AI Search | £0            |
| Azure OpenAI    | ~£2           |
| Container Apps  | <£1           |
| App Insights    | <£1           |
| **Total**       | **~£3/month** |

Comfortably inside Azure's £200 free-trial credit.

### 9.3 Decommission mode

```bash
azd down --purge --force-delete
```

Cost: ~£0/month (only the resource group remains, free).

`azd up` brings everything back in 8–12 minutes.

### 9.4 What this is **not** sized for

This stack is not sized to be a component of high-traffic production services. Doing that would force AI Search to Basic tier (£60/month base) and push monthly cost to ~£180–£215. For higher-throughput deployments, scale tiers up via the `searchSku` parameter in `main.parameters.json` and revisit Container App replica caps.

## 10. Open questions / deferred decisions

- **Semantic ranker** — Azure AI Search semantic ranker improves relevance but requires Basic+ tier. Deferred to a future v0.2 of this repo.
- **Streaming responses** — `POST /query` currently returns the full answer. Server-sent events deferred; mentioned in README as a contribution opportunity.
- **Multi-language corpus** — current scope is English UK regulatory. Bilingual extension deferred.
- **Tenant admin UI** — no UI in v1. Ingest is API-only. A small admin frontend is deferred.

## 11. Out of scope

- LangChain (LangGraph only)
- AKS (Container Apps only)
- Index-per-tenant (documented as escalation path, not implemented)
- Anything that touches `mcp-llm-eval`'s source — it's consumed from PyPI at `>=0.7.0,<0.8.0`, never modified from this repo
- Anything that touches `llm-benchmarks` source code — only artefact files (`azure-summary.json`, `azure-benchmark.json`) are written there by CI
- Manual versioning / manual changelog editing — Release Please owns both
- Private or proprietary data — corpus is exclusively public regulatory documents

## 12. Definition of done

- [ ] `azd up` from a clean checkout provisions the full stack and returns a working URL
- [ ] `curl` against that URL with a valid JWT returns a grounded answer with citations
- [ ] `mcp-llm-eval evaluate-rag` against the live endpoint passes the gate thresholds
- [ ] CI is green on main; eval-gate workflow runs nightly and posts results
- [ ] README explains: what it is, who it's for, how to run it, how to fork it, security posture, cost expectations
- [ ] `docs/architecture.md` has at least one diagram showing data flow
- [ ] Two integration tests prove tenant isolation
- [ ] Repo has: MIT licence, CHANGELOG, CONTRIBUTING, security scanning enabled, branch protection on main

## 13. Integration with the broader ecosystem

`rag-on-azure` plugs into a four-stage benchmark pipeline maintained across `mcp-llm-eval`, `llm-benchmarks`, and `llmshot`:

1. **Producer** (any RAG project) ships a golden dataset
2. **Engine** (`mcp-llm-eval`) evaluates retrieval quality against the dataset
3. **Data layer** (`llm-benchmarks` repo) stores the resulting JSON artefacts
4. **Visualisation** (`llmshot`) renders results as a leaderboard tab

`rag-on-azure` joins as a retrieval dataset alongside the existing entries:

| Existing entries            | New entry            |
| --------------------------- | -------------------- |
| `bm25-summary.json`         | `azure-summary.json` |
| `openai-small-summary.json` |                      |
| `openai-large-summary.json` |                      |
| `google-summary.json`       |                      |

### 13.1 Output artefacts

Every CI run on `main` produces two JSON artefacts and pushes them to `llm-benchmarks` (separate repo, write via fine-grained GitHub App token, scoped):

- `llm-benchmarks/retrieval/azure-summary.json` — aggregate metrics (recall@k, MRR, nDCG, citation faithfulness, p50/p95 latency)
- `llm-benchmarks/retrieval/azure-benchmark.json` — full per-query breakdown for drill-down views in LLMShot

The shape matches the schema produced by the existing retrievers — `mcp-llm-eval >=0.7.0` emits this shape natively. No translator layer required.

### 13.2 Optional integration

The `llm-benchmarks` push step is optional. Forks unconnected to that ecosystem can disable it via a CI variable. `rag-on-azure` runs independently regardless.

---

**Implementation order:**

1. Repo skeleton, Release Please bootstrap, `.gitignore`, pre-commit, design spec committed
2. Bicep modules (§2) with `what-if` validation; first `azd up` against dev
3. Ingest pipeline (§4); seed corpus into the live index
4. `LLMClient` + `TenantAwareSearchClient` adapters (§3.3) with full unit-test coverage
5. LangGraph nodes + FastAPI surface (§3); first end-to-end live query
6. JWT auth + tenant isolation tests (§5.3); deploy app image
7. GitHub Actions CI (§6.1) including OIDC federation, eval gate, and the cross-repo push to `llm-benchmarks` (§13.1)
8. Azure Pipelines mirror (§6.2); polish docs (architecture, security, deployment)
9. README pass, screenshots, CHANGELOG, tag v0.1.0

Each step generates one or more PRs. CI must be green before merging.
