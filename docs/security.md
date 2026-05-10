# Security model and hardening notes

Threat model, secret inventory, tenant isolation invariant, and the
posture upgrades deferred from v1. The v1 posture is acceptable for a
dev demo: scope-limited resource group, no production data, branch
protection on main as the load-bearing control. Anything in this
document marked "hardening upgrade" is a step toward promoting the
stack beyond that scope.

## Threat model

Per design spec [`§7.1`](design/rag-on-azure.md). The stack defends
against the following threats:

| Threat | Mitigation |
| --- | --- |
| API key leaked in repo | Managed identity end-to-end; `gitleaks` pre-commit + push protection; `.env*` gitignored; secrets never appear in Bicep parameter files (`infra/main.parameters.json` is gitignored). |
| Long-lived CI secret leaked | OIDC federation to Azure AD via GitHub Actions; no service principal secret is stored. The cross-repo `publish-benchmarks` job uses a GitHub App install token minted per run via `actions/create-github-app-token@v1`, not a PAT. |
| Cross-tenant data leak | `TenantAwareSearchClient.hybrid_search` makes the `tenant_id` argument non-optional at both runtime and `mypy --strict` time. Two integration tests prove the boundary holds (see "Tenant isolation invariant" below). |
| Prompt injection extracts other tenants' data | Tenant filter applied at the retrieval boundary, before the LLM ever sees chunks. The LLM cannot retrieve what `TenantAwareSearchClient` never returned. |
| Citation hallucination | The `generate` node validates every cited chunk ID against the retrieved set; one strict-prompt retry, then `CitationContractError` → HTTP 502. The empty-retrieval short-circuit returns a fixed answer without the LLM if no chunks were retrieved (defense in depth against the LLM filling in from training data). |
| Excessive token spend | Per-deployment TPM quota on Azure OpenAI (50k chat, 30k embedding for the dev SKU) is the hard ceiling; no per-tenant token tracking yet (queued — see hardening below). |
| Denial-of-wallet | Container App max replicas = 3; budget alert at £40 on the dev subscription. |
| Image-pull regression to a mutable tag | `containerImage` is a required Bicep parameter with no default; every deploy path passes `sha-<short>` explicitly. Closes the prior `make apply` quirk where local applies could regress to `latest-dev`. |

## Secret inventory

What lives where, who can read it, how it rotates.

| Secret | Storage | Read by | Write by | Rotation |
| --- | --- | --- | --- | --- |
| `jwt-signing-key` (RSA-2048 PEM) | Azure Key Vault `kv-${prefix}-${env}-${suffix}` | Container App managed identity (`Key Vault Secrets User` role) | Operator only. Bicep declares the secret resource but never sets its value — see [`AGENTS.md`](../AGENTS.md) `jwt-signing-key is operator-managed`. | Manual today: rotate the secret in Key Vault, restart the Container App. The auth verifier caches the public PEM for 5 minutes. |
| `OPENAI_API_KEY` (eval-gate only) | GitHub Actions secret | `eval-gate` job in `ci.yml` | Operator (`gh secret set`) | Manual; coverage limited to the eval-gate's gpt-4o-mini calls. The deployed app uses Azure OpenAI via managed identity, not this key. |
| `LLMSHOT_APP_PRIVATE_KEY` (GitHub App PEM) | GitHub Actions secret | `publish-benchmarks` job in `ci.yml` | Operator | App-side rotation: regenerate the App's private key in GitHub UI, replace the secret. |
| `AZURE_CLIENT_ID` (CI federated identity SP) | GitHub Actions repo variable | OIDC login steps in `ci.yml` | Bootstrap script (`scripts/bootstrap-oidc.sh`) | The principal itself doesn't hold a secret; rotation = re-running bootstrap to issue a new federated credential subject. |
| Azure resource credentials (managed identity tokens) | Per-resource MI; not persisted | All Azure SDK calls from the Container App | Azure platform | Auto, per token TTL. |

**No long-lived shared secret exists in the deployed runtime.** The
container app authenticates to Azure OpenAI, AI Search, and Key Vault
exclusively via its managed identity. There is no path where an
exfiltrated container image yields callable credentials.

## Tenant isolation invariant

The single most load-bearing claim of this demo is that one tenant
cannot retrieve another tenant's chunks. The invariant is enforced at
the retrieval boundary (not at the application or LLM layer), with two
audit-grade tests as the credibility floor.

- Boundary: `app/src/rag_on_azure/clients/search.py::TenantAwareSearchClient.hybrid_search`.
- Mechanism: `tenant_id` is positional-or-keyword non-optional. The
  OData filter `tenant_id eq '<id>'` is composed *inside* the client
  with single-quote doubling and a strict `^[a-z0-9-]+$` validator.
  User-supplied filters are AND-appended after the tenant clause.
- Tests:
  - `app/tests/unit/test_search.py::test_cross_tenant_leak_prevented`
  - `app/tests/unit/test_search.py::test_missing_tenant_id_raises`

Any change to this boundary requires explicit acknowledgment in the
PR description. Both tests live in `app/tests/unit/` (not integration)
because they assert structural invariants of the client, not the
end-to-end network call.

## API auth posture per route

| Route | Auth | Reason |
| --- | --- | --- |
| `POST /query` | RS256 JWT signature verified against the Key Vault public PEM; `tenant_id` claim required | Multi-tenant retrieval; tenant_id flows into the GraphState. |
| `GET /healthz` | None | Container Apps liveness probe. Auth-free by design. |
| `GET /readyz` | None | Container Apps readiness probe. Auth-free by design. |
| `GET /metrics` | None (public) | Phase 3 D1 verdict — matches standard Prometheus scrape posture and the demo "show this works" intent. **Production hardening upgrade** in the section below. |
| `POST /ingest` | RS256 JWT + `tenant_admin: true` claim | Admin gate via `get_current_admin`. The route does not accept a body-side tenant_id; ingest writes per the manifest's per-source tenant_id. |

Local-dev convenience: `ENABLE_DEV_AUTH=true` bypasses signature
verification (still requires `tenant_id` in the JWT). Off by default;
logged loudly at boot when on.

## Tooling

- `gitleaks` runs as a pre-commit hook AND a CI job (pinned to
  matching versions in `.pre-commit-config.yaml` and `ci.yml`).
- GitHub repo settings: secret scanning **on**, push protection **on**,
  Dependabot **on** (covers github-actions and pip ecosystems).
- `pip-audit` weekly scheduled run — queued for v0.x (issue
  forthcoming).

## Hardening upgrades deferred from v1

None of these are required for the dev demo. They are the steps to
take before promoting the stack to handle real traffic or real data.

### Two-app-registration split for CI federated identity

Phase 1 ships a single AAD app registration (`rag-on-azure-ci`) with
two federated credentials:

- subject `repo:OWNER/REPO:ref:refs/heads/main` — used by deploy +
  eval-gate
- subject `repo:OWNER/REPO:pull_request` — used by bicep what-if

Both credentials map to the same service principal, which holds
`Owner` on the dev resource group. The role assignment is
workflow-gated, not principal-gated: `if:` conditions in
`.github/workflows/ci.yml` enforce that the deploy job runs only on
main pushes. PRs technically share the same principal at the role
level. Branch protection on main + required PR reviews are the
primary defense — a PR that altered `ci.yml` to run deploy on PR
events would still need a reviewer to merge it before the new
workflow could take effect.

#### Hardening upgrade

Split into two app registrations.

- `rag-on-azure-ci-pr` — federated to `:pull_request`, granted
  `Reader` on the dev RG. Sufficient for `az deployment group
  what-if` (read-only simulation). Cannot deploy even if the workflow
  YAML is compromised.
- `rag-on-azure-ci-main` — federated to `:ref:refs/heads/main`,
  granted `Owner` on the dev RG. Used by deploy + eval-gate.

`scripts/bootstrap-oidc.sh` would extend to provision both apps, and
`ci.yml` would reference different `client-id` values per job — the
PR-running jobs use the PR app, the main-running jobs use the main
app.

This is the right posture before any deployment beyond dev.

### `/metrics` endpoint authentication

`/metrics` is currently public. For a real Prometheus deployment the
acceptable postures are:

- **Network-level allowlist.** Container Apps ingress accepts only
  the Prometheus scraper's source IP/range. Cheapest in code; needs
  Container Apps Environment-level configuration.
- **Admin-JWT bearer.** Add `Depends(get_current_admin)` to the
  `/metrics` route. Requires the scraper to mint short-lived tokens.

Either lands as a single-commit follow-up. The demo posture stays
public until then.

### Per-tenant token-spend tracking

Today the only ceiling on token spend is the deployment-level TPM
quota. A noisy tenant can starve quieter tenants. The right
production posture is per-tenant token accounting (a Counter labelled
by `tenant_id`, surfaced via `/metrics`, with a per-tenant
rate-limiter at the FastAPI middleware layer reading those counters).
Out of scope for v1.

### Container Apps Job for `/ingest`

The Phase 5 `POST /ingest` route schedules the corpus pipeline as a
FastAPI background task. Container Apps scale-to-zero applies an idle
timeout (default 5 min); a background task can be killed mid-run.
The pipeline is idempotent (content-hash sweep), so a killed run can
be retried, but for guaranteed completion the prod-grade move is a
dedicated Container Apps Job (designed for batch work, no
scale-to-zero kill semantics). See [`AGENTS.md`](../AGENTS.md)
operational quirks and design spec [`§4.5`](design/rag-on-azure.md).

### `ENABLE_DEV_AUTH` removal

The Day 5 kill-switch that bypasses JWT signature verification still
exists in `auth.py`. Off by default and logged loudly at boot when
on, but a removal pass is queued: prod-mode signature verification
is now the default and the unsigned path no longer earns its keep.

### Long-running secret rotation

`jwt-signing-key` rotation is manual today. The right production
posture is a scheduled rotation job (Azure Functions or a Container
Apps Job on cron) plus a verifier that accepts both current and
previous PEMs during the rollover window.

## Two-app-registration split for CI federated identity

Phase 1 ships a single AAD app registration (`rag-on-azure-ci`) with two
federated credentials:

- subject `repo:OWNER/REPO:ref:refs/heads/main` — used by deploy + eval-gate
- subject `repo:OWNER/REPO:pull_request` — used by bicep what-if

Both credentials map to the same service principal, which holds `Owner`
on the dev resource group. The role assignment is workflow-gated, not
principal-gated: `if:` conditions in `.github/workflows/ci.yml` enforce
that the deploy job runs only on main pushes. PRs technically share the
same principal at the role level. Branch protection on main + required
PR reviews are the primary defense — a PR that altered `ci.yml` to run
deploy on PR events would still need a reviewer to merge it before the
new workflow could take effect.

### Hardening upgrade

Split into two app registrations.

- `rag-on-azure-ci-pr` — federated to `:pull_request`, granted `Reader`
  on the dev RG. Sufficient for `az deployment group what-if` (read-only
  simulation). Cannot deploy even if the workflow YAML is compromised.
- `rag-on-azure-ci-main` — federated to `:ref:refs/heads/main`, granted
  `Owner` on the dev RG. Used by deploy + eval-gate.

`scripts/bootstrap-oidc.sh` would extend to provision both apps, and
`ci.yml` would reference different `client-id` values per job — the
PR-running jobs use the PR app, the main-running jobs use the main app.

This is the right posture before any deployment beyond dev.

For v1, single-app + workflow-gating is acceptable: the dev RG is
scope-limited (no production data, no production traffic), and branch
protection on main is the load-bearing control.

---

See also: [`docs/architecture.md`](architecture.md) for the request-flow
diagram, [`docs/deployment.md`](deployment.md) for the day-1 runbook,
and [`AGENTS.md`](../AGENTS.md) for operational quirks that touch
security posture (the `jwt-signing-key` ownership boundary in
particular).
