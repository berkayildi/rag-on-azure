# Deployment runbook

Day-1 walkthrough for standing this stack up from a clean checkout to
a working `https://${dev_fqdn}/query` endpoint with eval-gate green
on main. Every step assumes the operator has the prerequisites listed
below and is logged into the right Azure tenant.

For routine operations after the first deploy (rotating keys,
re-ingesting the corpus, watching CI), see [`AGENTS.md`](../AGENTS.md).
For the threat model and security posture, see
[`docs/security.md`](security.md). For component-level architecture,
see [`docs/architecture.md`](architecture.md).

## Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| `az` (Azure CLI) | ≥ 2.60 | `az login` against the right tenant before starting. |
| `azd` (Azure Developer CLI) | ≥ 1.10 | Used by `make apply` and the deployment runbook below. |
| Bicep | ≥ 0.27 | `az bicep install` fetches the latest. |
| Python | 3.12 | Required by app + ingest packages. |
| Docker | any modern | Local build / image inspection. |
| `gitleaks` | 8.21.2 | Pre-commit hook + CI step share this version. |
| `gh` (GitHub CLI) | ≥ 2.40 | Used by the OIDC bootstrap script and operator setup steps. |
| Azure subscription | Active, payment method on file | The dev RG provisions in ~3 minutes; budget alert at £40. |

Region selection matters. The current default is **Sweden Central**
because of model availability in May 2026 (full reasoning in design
spec [`§0`](design/rag-on-azure.md)). Confirm regional availability
**and** quota of `gpt-4o`, `text-embedding-3-small`, and Azure AI
Search before committing infra. UK South with `gpt-4o-mini` is the
long-term target if quota and SKU availability return there.

## Step 1 — Repo bootstrap

```bash
git clone git@github.com:berkayildi/rag-on-azure.git
cd rag-on-azure

python -m venv .venv
source .venv/bin/activate
pip install -e "app[dev]" -e "ingest[dev]"

pre-commit install
pytest                         # all unit + integration green
```

If anything fails here, fix it before touching Azure. The local test
suite is the canonical correctness check; CI runs the same tests.

## Step 2 — Azure environment

```bash
az login
azd auth login
azd env new dev                  # creates a new azd environment named "dev"
azd env set AZURE_LOCATION swedencentral
azd env set AZURE_RESOURCE_GROUP rg-dev
```

`azd env new` provisions a `.azure/dev/` directory with environment
variables that subsequent commands read. The location and RG values
above match the defaults baked into `infra/main.parameters.example.json`.

## Step 3 — Bicep parameter file

Copy the example to the gitignored real file and fill in your values:

```bash
cp infra/main.parameters.example.json infra/main.parameters.json
```

Edit `infra/main.parameters.json` and set:

- `developerPrincipalId` — your AAD object ID, fetched via
  `az ad signed-in-user show --query id -o tsv`. This grants you
  `Search Index Data Contributor` on the search service so you can
  run `make ingest` locally.
- `containerImage` — required (no default). For the first deploy
  this can be the GHCR `latest-dev` tag if you've already triggered
  one CI build, or you can build + push locally first (see step 5
  below). The Bicep parameter is required precisely so a deploy can't
  silently regress to a mutable tag.
- `ciPrincipalId` — for the first manual deploy, leave empty. CI
  fills this in from `vars.AZURE_CI_PRINCIPAL_ID` after step 6.

## Step 4 — Provision infrastructure

```bash
make plan                        # az deployment group what-if; read-only
make apply                       # azd provision; ~3 minutes for a fresh RG
```

`make apply` provisions: Container Apps Environment + Container App,
Azure OpenAI account with `gpt-4o` + `text-embedding-3-small`
deployments, Azure AI Search (Free SKU), Key Vault, Log Analytics +
Application Insights. Output captures the FQDN, search endpoint,
OpenAI endpoint, and Key Vault URI.

```bash
make outputs                     # prints the FQDN and other endpoints
```

## Step 5 — Set the JWT signing key

Bicep declares the `jwt-signing-key` secret in Key Vault but **does
not set its value** — the secret is operator-managed (closes a
regression where every apply rotated the key; see
[`AGENTS.md`](../AGENTS.md)).

Generate a fresh RSA-2048 keypair and load the public PEM into Key
Vault:

```bash
# Local keypair generation (also used by scripts/mint-token.py)
openssl genrsa -out jwt-signing-key.pem 2048
openssl rsa -in jwt-signing-key.pem -pubout -out jwt-signing-key.pub.pem

# Set the public PEM as the Key Vault secret value
KV_NAME=$(make outputs | grep keyVaultName | awk '{print $2}')
az keyvault secret set \
  --vault-name "$KV_NAME" \
  --name jwt-signing-key \
  --file jwt-signing-key.pub.pem
```

Keep `jwt-signing-key.pem` (the **private** key) somewhere local and
out of the repo — `scripts/mint-token.py` reads it to mint dev tokens.
**Do not** commit either PEM. The `.gitleaks.toml` config and
push-protection rules will refuse a commit that includes them, but
defense in depth.

## Step 6 — Build and push the container image

The first manual deploy needs a real image in GHCR. From the repo
root:

```bash
SHA_SHORT=$(git rev-parse --short HEAD)
docker buildx build \
  --platform linux/amd64 \
  -f app/Dockerfile \
  -t ghcr.io/${GITHUB_USER}/rag-on-azure:sha-${SHA_SHORT} \
  --push \
  .
```

After the first deploy, every push to main builds and pushes the
image as part of the CI pipeline; this manual step is only for
bootstrapping.

Update `infra/main.parameters.json` to point at the sha-pinned tag
and re-run `make apply` to roll the Container App revision.

## Step 7 — Seed the corpus

The first ingest is operator-side; subsequent ingests can go through
either `make ingest` or the admin-gated `POST /ingest` route.

```bash
# Set the same env vars the deployed app uses
SEARCH_ENDPOINT=$(az search service list -g rg-dev --query '[0].name' -o tsv)
export AZURE_SEARCH_ENDPOINT="https://${SEARCH_ENDPOINT}.search.windows.net"
export AZURE_OPENAI_ENDPOINT="$(make outputs | grep openaiEndpoint | awk '{print $2}')"
export AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small
export AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4o

cd ingest
python -m ingest all             # fetch → chunk → index; ~3-5 min cold cache
```

Cost: ~£0.50–£1 of Azure OpenAI embedding tokens for a cold-cache
full run. Subsequent runs are near-free (content-hash sweep skips
unchanged chunks).

## Step 8 — Verify the deployed app

The canonical post-deploy check is a single `make` target that chains
`/healthz` → `/readyz` → a signed `/query` against the FCA Consumer
Duty sample question:

```bash
make smoke
```

The `smoke` target reads three variables (override on the command
line as needed):

- `FQDN` — the Container App FQDN, defaults to the `dev` environment.
  Pull a fresh value with `make outputs | grep containerAppFqdn` if
  you've redeployed under a different `azd env`.
- `SIGNING_KEY` — path to the RSA private PEM used to mint the dev
  JWT, defaults to `scripts/dev-keys/jwt-signing.private.pem`. The
  matching public PEM must be the current value of the
  `jwt-signing-key` Key Vault secret (step 5).
- `TENANT` — tenant claim baked into the minted token, defaults to
  `demo`.

```bash
# Against a non-default FQDN:
make smoke FQDN=rag-prod-ca.example.azurecontainerapps.io

# Individual steps for debugging (composable):
make smoke-healthz
make smoke-readyz
make smoke-token       # prints the JWT, useful for ad-hoc curl
make smoke-query

# Metrics (public, not part of the smoke chain):
FQDN=$(make outputs | grep containerAppFqdn | awk '{print $2}')
curl -s "https://${FQDN}/metrics" | head -20
```

If `smoke-query` returns a grounded answer with citations, the stack
is live.

## Step 9 — Bootstrap CI (OIDC federation)

Once for the lifetime of the repo. The `scripts/bootstrap-oidc.sh`
helper provisions the AAD app registration with both federated
credential subjects (deploy and bicep-whatif) and prints the
`AZURE_CLIENT_ID` to set as a repo variable.

```bash
./scripts/bootstrap-oidc.sh

# After the script prints the values:
gh variable set AZURE_CLIENT_ID --body <printed-value>
gh variable set AZURE_TENANT_ID --body <printed-value>
gh variable set AZURE_SUBSCRIPTION_ID --body <printed-value>
gh variable set AZURE_RESOURCE_GROUP --body rg-dev
gh variable set AZURE_LOCATION --body swedencentral
```

The federated credential subjects in the AAD app cover both
`refs/heads/main` (deploy + eval-gate) and `pull_request` (bicep
what-if). PR-side jobs and main-side jobs share the same service
principal today; the two-app split is documented in
[`docs/security.md`](security.md) as a hardening upgrade.

## Step 10 — Operator setup for eval-gate

The eval-gate job calls Azure OpenAI from the GitHub runner via
`mcp-llm-eval`'s OpenAI provider, which uses an OpenAI-direct API key
(not the managed identity used by the deployed app).

```bash
# OpenAI direct key for the eval-gate runner
gh secret set OPENAI_API_KEY      # paste your OpenAI API key

# CI principal SP object ID (for the Search Index Data Reader role)
SP_OBJECT_ID=$(az ad sp show --id $AZURE_CLIENT_ID --query id -o tsv)
gh variable set AZURE_CI_PRINCIPAL_ID --body "$SP_OBJECT_ID"
```

Re-deploy after setting `AZURE_CI_PRINCIPAL_ID` so the role
assignment lands.

## Step 11 — Operator setup for `publish-benchmarks`

The cross-repo push of eval results to `llm-benchmarks` uses a
GitHub App install token (see [`§13`](design/rag-on-azure.md) of the
design spec).

1. Create a GitHub App in your account: name e.g.
   `rag-on-azure-llmshot-publisher`. Permission: **Contents: read &
   write**. Scope: only the `llm-benchmarks` repo.
2. Generate the App private key, install the App on your account
   scoped to `llm-benchmarks`.
3. Set the GitHub Actions secret + variables:

   ```bash
   gh secret set LLMSHOT_APP_PRIVATE_KEY < path/to/app-private-key.pem
   gh variable set LLMSHOT_APP_ID --body <numeric-app-id>
   gh variable set LLMSHOT_PUSH_ENABLED --body true
   ```

The `publish-benchmarks` job is gated on `LLMSHOT_PUSH_ENABLED ==
'true'` and uses `continue-on-error: true`, so it is silently skipped
on forks where the variables aren't set, and never fails the CI run
on a transient push problem.

## Step 12 — First green CI run on main

Push a trivial commit to main (or merge the bootstrap PR) and watch
all 10 jobs of `ci.yml` go green: `lint → gitleaks → bicep-validate →
unit → integration → build → bicep-whatif → deploy → eval-gate →
publish-benchmarks`. Eval-gate measurements should land within the
calibrated thresholds defined in `eval/.eval-gate.yml`. The
publish-benchmarks job pushes the latest pointer + history pair to
`llm-benchmarks/retrieval/`.

If eval-gate trips on first run with the seeded corpus, the right
move is to recalibrate the thresholds (separate commit), not to
loosen `eval/golden.jsonl`. The golden dataset is the contract; the
thresholds are tunable.

## Tear-down

```bash
azd down --purge --force
```

Nukes the resource group cleanly. Cost reverts to ~£0/month (the
empty RG is free). `azd up` brings everything back in 8–12 minutes
provided you still have the same parameters.json and the JWT signing
key in your local store (re-load into the new Key Vault per step 5).

## Common things that bite you the first time

The full list lives in [`AGENTS.md`](../AGENTS.md) `## Operational
quirks`. The ones most likely to cost you minutes on a first deploy:

- **`Microsoft.App` resource provider drift.** `az provider register
  -n Microsoft.App --wait` is idempotent; run it if Container Apps
  operations fail with "subscription not registered".
- **Key Vault Secrets Officer is not Bicep-granted to you.** You'll
  need to grant yourself the role manually before step 5.
- **`make apply` regression to `latest-dev`** — closed in v1; the
  Bicep `containerImage` parameter is now required so this can't
  happen.
- **`bicep-whatif` shows `ciPrincipalId` role assignment as
  would-be-deleted on subsequent PRs.** Misleading visual after the
  first apply; real diff against live state is empty. Documented in
  AGENTS.md.
