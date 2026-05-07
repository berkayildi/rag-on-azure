# Security hardening notes

Day 8+ posture upgrades deferred from earlier phases. None are required
for v1; they are the next-level hardening steps to take before promoting
this stack beyond a dev environment.

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
