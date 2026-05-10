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

### First-time CI bootstrap (OIDC federation)

Before the first `ci.yml` run on a fork, federate GitHub Actions to Azure AD so workflows can authenticate without a long-lived service principal secret. Idempotent — safe to re-run.

Prerequisites: `az` logged in, `azd env new <name>` already run, `jq` available, `gh` CLI installed for the variable-set step. The running identity needs Application Administrator on the AAD tenant and Owner / User Access Administrator on the dev resource group.

```bash
./scripts/bootstrap-oidc.sh   # creates AAD app, two federated credentials, role assignment
```

The script prints the five `gh variable set` commands to run afterwards (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `AZURE_LOCATION`). All five are repo variables, not secrets — none are sensitive on their own.

The two federated-credential subjects are scoped per-purpose: `:ref:refs/heads/main` for deploy + eval-gate, `:pull_request` for bicep what-if. Workflow-level `if:` conditions enforce that PRs cannot reach the deploy job. See `docs/security.md` for the two-app-registration upgrade path.

### Tear-down

```bash
make down                    # azd down --purge --force, with confirmation countdown
```

### Local dev tools

Copy `.env.example` to `.env` and fill in real endpoints. The `.env` file is gitignored; only `.env.example` is committed.

The auth dependency has two modes, gated by `ENABLE_DEV_AUTH`:

- `ENABLE_DEV_AUTH=true` — unsigned tokens (`alg=none`) decode without signature verification. Local-dev convenience.
- `ENABLE_DEV_AUTH=false` (production) — RS256 signature verification against the public PEM held in Key Vault as the `jwt-signing-key` secret.

**Mint an unsigned token (dev-mode environments only):**

```bash
TOKEN=$(python scripts/mint-token.py --tenant-id demo)
```

Stderr emits a warning that the token is unsigned. Stdout still gets the token so `$()` capture works.

**One-time RSA keypair setup (for verified-mode testing or production):**

```bash
mkdir -p scripts/dev-keys
openssl genrsa -out scripts/dev-keys/jwt-signing.private.pem 2048
openssl rsa -in scripts/dev-keys/jwt-signing.private.pem -pubout -out scripts/dev-keys/jwt-signing.public.pem
```

The whole `scripts/dev-keys/` directory is gitignored — anyone forking generates their own keypair. The private key never leaves the developer's machine; only the public PEM is uploaded to Key Vault.

**Push the public key to Key Vault (one-time, before flipping the deployed CA to `ENABLE_DEV_AUTH=false`):**

```bash
KV_NAME=$(azd env get-value keyVaultName)
az keyvault secret set \
  --vault-name "$KV_NAME" \
  --name jwt-signing-key \
  --file scripts/dev-keys/jwt-signing.public.pem
```

**Mint a signed token for verified-mode testing:**

```bash
TOKEN=$(python scripts/mint-token.py --tenant-id demo \
  --signing-key-path scripts/dev-keys/jwt-signing.private.pem)
```

Pass via `Authorization: Bearer $TOKEN` against an environment running with `ENABLE_DEV_AUTH=false`. The verifier fetches the matching public PEM from Key Vault, caches it for five minutes, and validates the signature.

## Operational quirks

Surprises hit during live deployment that are stable enough to document. Add to this list when something costs 10+ minutes the first time and is likely to recur for the next operator.

### Microsoft.App resource provider can drift to NotRegistered

Symptom: `az containerapp update ...` returns *"Subscription is not registered for the Microsoft.App resource provider"* on a subscription where Container Apps are already running, or `az provider show -n Microsoft.App --query registrationState` returns `Registering` (mid-flight) instead of `Registered`.

Recovery — synchronous re-registration:

```bash
az provider register -n Microsoft.App --wait
```

Idempotent: safe to run when already `Registered`. Hit twice during Day 6 Phase 5; no clear pattern for what triggers drift, so just re-register and retry the failed command.

### Key Vault Secrets Officer is not Bicep-granted to the deploying developer

`infra/modules/keyvault.bicep` grants `Key Vault Secrets User` to the Container App's managed identity only (read-only at runtime). The human operator deploying the stack gets no role assignment by default, so the first `az keyvault secret set ...` (for example, populating `jwt-signing-key` with a PEM) returns `403 ForbiddenByRbac` with `Assignment: (not found)`.

One-time grant for the deploying identity, scoped to this Key Vault only:

```bash
USER_OID=$(az ad signed-in-user show --query id -o tsv)
SUB_ID=$(az account show --query id -o tsv)
RG=$(azd env get-value AZURE_RESOURCE_GROUP)
KV_NAME=$(azd env get-value keyVaultName)
KV_ID="/subscriptions/$SUB_ID/resourceGroups/$RG/providers/Microsoft.KeyVault/vaults/$KV_NAME"

az role assignment create \
  --role "Key Vault Secrets Officer" \
  --assignee "$USER_OID" \
  --scope "$KV_ID"
```

`Officer` (not `Administrator`) is the right scope — covers `setSecret` / `getSecret` / `listSecrets` without role/secret-management privileges. Requires `Owner` or `User Access Administrator` on the subscription (or the vault scope) to grant. Propagation: typically 30s–2min.

### `jwt-signing-key` is operator-managed — Bicep MUST NOT touch it

The `jwt-signing-key` secret in Key Vault is **never** declared as a Bicep resource and **never** parameterised through `main.bicep`. The vault itself is provisioned by `infra/modules/keyvault.bicep`; the secret value is populated and rotated exclusively out-of-band via the `az keyvault secret set --file …jwt-signing.public.pem` flow documented above.

Why this rule exists: an earlier iteration declared the secret in Bicep with a `newGuid()` default. Every `az deployment group create` (and every CI deploy) overwrote the rotated public PEM with a fresh GUID, breaking RS256 signature verification in production until the operator re-uploaded the PEM by hand. Recovered Day 6→7; the architectural fix is to keep secret values out of the IaC graph entirely.

If a future change appears to need Bicep-managed secrets (e.g. a `Microsoft.KeyVault/vaults/secrets@…` resource, or a `@secure()` param defaulting to `newGuid()` / `utcNow()`), stop and reconsider — the right answer is almost always an operator runbook entry here, not a Bicep resource.

### `az containerapp logs show --type system` rejects revision/container/replica filters

The `--type system` flag returns environment-level system logs (image-pull, scale events, replica scheduling) but does not accept the `--revision`, `--container`, or `--replica` filters that work for `--type console` (the default). Combining them errors. To target system logs from a specific revision, query Log Analytics directly via `az monitor log-analytics query` against the workspace `customerId` and filter on `RevisionName_s` in KQL.

### `make apply` locally would regress the running image to `latest-dev`

`infra/main.bicep` declares `param containerImage string = 'ghcr.io/berkayildi/rag-on-azure:latest-dev'`, and `infra/main.parameters.json` does not override it. CI's deploy job passes `--parameters containerImage=ghcr.io/berkayildi/rag-on-azure:sha-<short>` so CI deploys are correctly pinned to immutable sha tags. A local `make apply` (or any `azd provision` / `az deployment group create` that uses `main.parameters.json` without an override) would re-template the running revision back to mutable `latest-dev` — a regression away from the immutable-sha discipline.

Surfaced during the Day 7 Phase 1 keyvault `make plan` run as a `~ Modify` entry on `Microsoft.App/containerApps/rag-dev-ca` showing `image: "sha-d130e51" => "latest-dev"`. The drift exists on `main` independent of any Bicep edit: it's a property of the parameter wiring, not of the resources themselves.

**Mitigation today:** do not run `make apply` locally against the dev (or any) resource group. All apply operations go through CI, which supplies the sha override. Local `make plan` is fine — read-only.

**Permanent fix queued (separate commit, out of scope of the keyvault PR):** either (a) replace the `latest-dev` default with a sentinel that errors when not explicitly overridden, forcing every deploy path to declare a tag; or (b) commit the current sha to `infra/main.parameters.json` and add an automated bump pattern (CI updates the parameters file as part of the deploy job). Option (a) is simpler and harder to drift from.

### `bicep-whatif` shows `ciPrincipalId` role assignment as would-be-deleted on subsequent PRs

The `eval-gate` job needs the OIDC-federated CI service principal to have `Search Index Data Reader` on the search service; this is granted by a conditional role assignment in `infra/modules/search.bicep` driven by the `ciPrincipalId` parameter. CI's `deploy` step passes `ciPrincipalId=${{ vars.AZURE_CI_PRINCIPAL_ID }}` so the assignment lands. CI's `bicep-whatif` step does NOT pass that override — it relies on `infra/main.parameters.example.json`, which has `"ciPrincipalId": { "value": "" }`.

Result: after main has the role assigned in the live RG, every subsequent PR's `bicep-whatif` re-templates with empty `ciPrincipalId`, the role assignment resource is conditional (`if (!empty(ciPrincipalId))`), and what-if reports it as `~ Delete`. Visual noise, not actual drift — `deploy` will re-create with the override on merge.

**Fix queued (separate commit):** thread `vars.AZURE_CI_PRINCIPAL_ID` through to the `bicep-whatif` step's `--parameters` overrides so what-if and deploy share the same parameter set.

### `.pre-commit-config.yaml` `additional_dependencies` is structurally coupled to `pyproject.toml` but not auto-synced

The `mypy` hook in `.pre-commit-config.yaml` runs in a pre-commit-managed isolated env and pins each typed dep separately under `additional_dependencies` (lines ~30–46). Those pins do NOT auto-update from `pyproject.toml` widening. When dependabot widens a range in `app/pyproject.toml` or `ingest/pyproject.toml`, CI installs the new major (because CI runs `pip install -e ./app -e ./ingest` against the live ranges) but local pre-commit's mypy still resolves to the old major (because pre-commit uses its own constrained range).

Symptoms when this drifts:
- CI lint fails with errors that local pre-commit didn't catch (e.g. `Unused "type: ignore"` when the new dep ships stubs the old one didn't).
- Local pre-commit fails with errors CI doesn't see (e.g. `module is installed, but missing library stubs` when the new dep depends on stub packaging the old version lacked).

Hit twice during Day 7 Wave 2: markdownify 0.13→2 (#15) and azure-search-documents 11→12 (#12). Both required a fix-forward that bumped the matching `additional_dependencies` entry alongside the code change.

**Mitigation today:** every dependabot bump that widens a pyproject range also needs a parallel widen in `.pre-commit-config.yaml`. Done manually, easy to forget.

**Day 8 options to consider:**
1. Add a CI lint check that diff-greps `pyproject.toml` ranges against `.pre-commit-config.yaml` ranges and fails if they drift.
2. Drop typed deps from `additional_dependencies` and accept `[import-untyped]` errors via a global mypy ignore — loses some type safety but eliminates the coupling.
3. Switch the mypy hook to `language: system` so it uses the project venv (where the real `pip install -e` lives) — eliminates the isolated env entirely, but requires every developer to maintain a current venv before committing.

### Lint short-circuit hides downstream signal in serial dep waves

`ci.yml`'s `unit-tests`, `integration-tests`, and `build` jobs declare `needs: [lint, gitleaks, bicep-validate]`. When mypy fails, the whole post-lint chain (tests, build, deploy, eval-gate) is skipped. In a serial dep-bump wave (multiple back-to-back merges to main), one major's lint failure swallows downstream verification for every subsequent merge — even though those merges might be runtime-clean against their own changes.

Hit during Day 7 Wave 2: PR #12 (azure-search-documents/app) broke lint; PRs #7 (openai/ingest), #10 (openai/app), and #16 (langgraph) all merged after it and inherited the same schema.py mypy failure, masking any of their own potential breakage. Once #12's root cause was fixed, all four were verified at once.

**Day 8 options to consider:**
1. Weaken `needs:` on `unit-tests`/`integration-tests` to `[gitleaks, bicep-validate]` only — accepts non-lint-clean code into the test stage and trades discipline for faster signal during multi-merge sequences.
2. Add a separate `runtime-checks` job that depends on `[gitleaks, bicep-validate]` only and runs a minimal smoke import (`python -c "import rag_on_azure; import ingest"`) — gives early signal on import-time breakage without skipping lint as the gate.
3. Stop merging dependabot PRs serially and batch them into a single merge train PR via dependabot's grouped-updates feature — single CI run, single fix, single rollback boundary.

### `azure-search-documents` app/ingest version skew is silent until both ranges admit the same major

`app/pyproject.toml` and `ingest/pyproject.toml` both declare `azure-search-documents` ranges. Pip resolves to the intersection. If only one side is widened (e.g. ingest goes `<13` while app stays `<12`), pip keeps installing the lower major and breakage stays hidden until BOTH sides admit the new major. Dependabot raises one PR per package per directory, so the two PRs land in arbitrary order — and the breakage often surfaces only on the second merge, even though the root cause is in code that the first merge "should have" exercised.

Hit Day 7 Wave 2: #14 (ingest) widened to `<13` but resolved to 11.6 because app was still `<12`. #12 widened app to `<13`, pip flipped to 12.0, and the `SearchFieldDataType` Enum break in `ingest/src/ingest/schema.py` surfaced under #12's CI run rather than #14's.

**Mitigation today:** when reviewing dependabot bumps for libraries that appear in both `app/` and `ingest/`, treat the second-merged PR as the actual integration test — that's where the resolver flip happens. Do not assume the first PR's green CI proves the bump is safe.

**Permanent fix queued:** consolidate shared dep ranges into a single source (e.g. a top-level `requirements.shared.txt` referenced from both pyprojects) so dependabot raises one PR per shared package, not per directory. Out of scope for Day 7.

### `POST /ingest` background task can be killed mid-run by Container Apps scale-to-zero

The Phase 5 `/ingest` route schedules the corpus pipeline as a FastAPI `BackgroundTasks` callback. Background tasks run AFTER the HTTP response is returned, in the same uvicorn worker process. Container Apps scale-to-zero applies an idle timeout (default 5 min of no traffic) and terminates the replica — including any in-flight background task. Ingest takes minutes to half an hour; any of those minutes can land on the kill window.

**Mitigation today:** the pipeline is idempotent (content-hash sweep in `ingest.index`), so a killed run can be retried and only the unfinished chunks get re-processed. Operators who want guaranteed completion should keep traffic alive (`curl /healthz` from cron during ingest), or temporarily set `min replicas = 1` on the Container App for the duration of the ingest.

**Permanent fix queued:** move ingest to a dedicated Container Apps **Job** (designed for batch work, no scale-to-zero kill semantics). Out of scope for the demo posture; documented as the prod-grade upgrade path in design spec §4.5.

### `POST /ingest` costs ~£0.50–£1 of Azure OpenAI embedding tokens per full run

The current `corpus_manifest.yaml` produces ~931 chunks. A cold-cache ingest embeds all of them via `text-embedding-3-small` (one batched call per upload batch of 100). On the dev SKU pricing this lands around £0.50–£1.00 per full re-ingest. Subsequent runs against an unchanged corpus are near-free because `ingest.index`'s content-hash sweep skips the embedding call.

**Mitigation today:** none structural — admins should be aware that hitting `POST /ingest` repeatedly burns embedding budget. Eval-gate's £25 build-phase budget assumed a single one-shot seed; multiple full re-ingests can erode that envelope.

**Permanent fix queued:** none. The cost is the work; idempotency already minimises it.

## Code conventions

- **Python 3.12**, type-hinted, `mypy --strict` clean
- **Ruff** for lint and format (config in `pyproject.toml`)
- **Pydantic** for all data models — never raw dicts crossing module boundaries
- **Async-first** — all I/O is async; tests use `pytest-asyncio`
- When using the `.aio` modules from `azure-identity` or `azure-search-documents`, add `aiohttp>=3.10,<4` as a runtime dependency. It's the async transport these libraries use at runtime — gated behind the `[aio]` extra in their packaging but required when async classes are instantiated
- **No LangChain** — LangGraph only (see `docs/design/rag-on-azure.md` §11)
- **No raw API keys in code or env** — managed identity via `DefaultAzureCredential` is the only deployed-stack auth path
- When adding a runtime dependency to any `pyproject.toml`, also add it (with the matching version pin) to `.pre-commit-config.yaml`'s mypy hook `additional_dependencies` — the hook runs in an isolated env and needs the same deps to type-check imports

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

- `ci.yml` runs lint → unit → integration → secrets scan → build → bicep what-if → deploy dev → eval gate → publish-benchmarks
- The `eval-gate` stage in `ci.yml` runs on every push to main: snapshots the live AI Search index (one tenant) to a JSONL via `eval/snapshot_corpus.py`, then runs `mcp-llm-eval evaluate-rag` against the snapshot. In-process eval, not a live HTTP-endpoint hit — see design spec §6.4 for the rationale.
- The `publish-benchmarks` stage pushes the eval-gate JSON output to the `llm-benchmarks` repo (latest pointers + timestamped history) via a GitHub App install token. Gated on `vars.LLMSHOT_PUSH_ENABLED == 'true'` and `continue-on-error: true` — best-effort, skipped silently on forks. See design spec §13.
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

## Known limitations

Docker image dependency installation uses `pyproject.toml` ranges, not a lockfile. Same-commit reproducibility holds within a transitive-version window. Add `uv lock` + lockfile install if reproducibility becomes audit-critical.

## Related projects

- `mcp-llm-eval` — evaluation engine, consumed from PyPI at `==0.9.2`. Never modified from this repo.
- `llm-benchmarks` — data layer. CI writes `azure-summary.json` and `azure-benchmark.json` to it. No source code changes.
- `llmshot` — visualisation. Consumes `llm-benchmarks` data. No coupling from this repo.
