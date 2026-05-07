#!/usr/bin/env bash
# bootstrap-oidc.sh — one-time setup of GitHub Actions → Azure AD OIDC federation.
#
# Idempotent. Safe to re-run. All mutations are guarded by existence checks.
#
# Provisions, in the current azd-env's subscription:
#   - One Azure AD application + service principal (display name configurable
#     via APP_NAME, defaults to "rag-on-azure-ci")
#   - Two federated credentials on that app:
#       * subject repo:OWNER/REPO:ref:refs/heads/main      (used by deploy + eval-gate)
#       * subject repo:OWNER/REPO:pull_request             (used by bicep what-if on PRs)
#   - Contributor role assignment for the SP on the dev resource group
#
# Prints the 5 GitHub repo variables the operator must set via `gh variable set`
# (or the GitHub web UI). The script does NOT call `gh` itself — operator runs
# those commands manually after reviewing the output, matching the
# Day-6-PEM-upload review discipline.
#
# Prerequisites: az logged in, azd env initialised (`azd env new dev` already
# run), gh CLI available for the post-script variable-set step. A git remote
# named "origin" pointing at github.com is used to derive owner/repo.
#
# Required Azure permissions: the running identity needs Application
# Administrator (or higher) on the AAD tenant to create app registrations,
# and Owner / User Access Administrator on the target resource group to
# create role assignments.
#
# Default role on the dev RG is Owner. Contributor alone is insufficient
# because Bicep deployments here create role assignments (Container App
# managed identity -> Key Vault Secrets User), which require
# Microsoft.Authorization/roleAssignments/write — not granted by Contributor.
# Strict-RBAC operators who want the Contributor + User Access Administrator
# split can run:
#   ROLE=Contributor ROLE_EXTRA=UserAccessAdministrator ./scripts/bootstrap-oidc.sh
# (ROLE_EXTRA is documented as the override path; the v1 script honours
# only ROLE, not ROLE_EXTRA — a future hardening pass can add the second
# assignment loop if needed.)

set -euo pipefail

APP_NAME="${APP_NAME:-rag-on-azure-ci}"
ROLE="${ROLE:-Owner}"
ISSUER="https://token.actions.githubusercontent.com"
AUDIENCE="api://AzureADTokenExchange"

log()  { printf '==> %s\n' "$*" >&2; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# --- preflight ---------------------------------------------------------------

command -v az >/dev/null  || die "az CLI not found"
command -v azd >/dev/null || die "azd CLI not found"
command -v jq >/dev/null  || die "jq not found (required for federated-credential JSON)"

az account show >/dev/null 2>&1 || die "az not logged in — run 'az login' first"

AZD_SUB="$(azd env get-value AZURE_SUBSCRIPTION_ID 2>/dev/null || true)"
AZD_RG="$(azd env get-value AZURE_RESOURCE_GROUP  2>/dev/null || true)"
AZD_LOC="$(azd env get-value AZURE_LOCATION       2>/dev/null || true)"
[ -n "$AZD_SUB" ] || die "AZURE_SUBSCRIPTION_ID not set on azd env — run 'azd env new <name>' first"
[ -n "$AZD_RG"  ] || die "AZURE_RESOURCE_GROUP not set on azd env"
[ -n "$AZD_LOC" ] || die "AZURE_LOCATION not set on azd env"

CUR_SUB="$(az account show --query id -o tsv)"
if [ "$CUR_SUB" != "$AZD_SUB" ]; then
  die "az current subscription ($CUR_SUB) does not match azd env subscription ($AZD_SUB) — run 'az account set --subscription $AZD_SUB'"
fi

TENANT_ID="$(az account show --query tenantId -o tsv)"

REMOTE_URL="$(git config --get remote.origin.url || true)"
[ -n "$REMOTE_URL" ] || die "git remote 'origin' not set — cannot derive owner/repo"
# Strip protocol prefix and .git suffix, then split owner/repo from the path.
OWNER_REPO="$(echo "$REMOTE_URL" | sed -E 's#^(https://github\.com/|git@github\.com:)##; s#\.git$##')"
case "$OWNER_REPO" in
  */*) : ;;
  *)   die "remote 'origin' does not look like a github.com URL: $REMOTE_URL" ;;
esac
OWNER="${OWNER_REPO%%/*}"
REPO="${OWNER_REPO##*/}"

log "subscription : $AZD_SUB"
log "tenant       : $TENANT_ID"
log "resource grp : $AZD_RG ($AZD_LOC)"
log "github repo  : $OWNER/$REPO"
log "AAD app name : $APP_NAME"
log "role         : $ROLE on the resource group"
echo

# --- AAD app + service principal ---------------------------------------------

CLIENT_ID="$(az ad app list --display-name "$APP_NAME" --query '[0].appId' -o tsv 2>/dev/null || true)"
if [ -z "$CLIENT_ID" ]; then
  log "creating AAD app '$APP_NAME'"
  CLIENT_ID="$(az ad app create \
    --display-name "$APP_NAME" \
    --sign-in-audience AzureADMyOrg \
    --query appId -o tsv)"
else
  log "AAD app '$APP_NAME' already exists (clientId $CLIENT_ID) — reusing"
fi

SP_OBJECT_ID="$(az ad sp list --filter "appId eq '$CLIENT_ID'" --query '[0].id' -o tsv 2>/dev/null || true)"
if [ -z "$SP_OBJECT_ID" ]; then
  log "creating service principal for clientId $CLIENT_ID"
  SP_OBJECT_ID="$(az ad sp create --id "$CLIENT_ID" --query id -o tsv)"
else
  log "service principal already exists (objectId $SP_OBJECT_ID) — reusing"
fi

# --- federated credentials ---------------------------------------------------

ensure_fc() {
  local name="$1" subject="$2" description="$3"
  local existing
  existing="$(az ad app federated-credential list --id "$CLIENT_ID" \
    --query "[?name=='$name'].name" -o tsv 2>/dev/null || true)"
  if [ -n "$existing" ]; then
    log "federated credential '$name' already exists — reusing"
    return
  fi
  log "creating federated credential '$name' (subject: $subject)"
  local payload
  payload="$(jq -n \
    --arg name "$name" \
    --arg issuer "$ISSUER" \
    --arg subject "$subject" \
    --arg description "$description" \
    --arg audience "$AUDIENCE" \
    '{name:$name, issuer:$issuer, subject:$subject, description:$description, audiences:[$audience]}')"
  az ad app federated-credential create --id "$CLIENT_ID" --parameters "$payload" >/dev/null
}

ensure_fc \
  "${APP_NAME}-main" \
  "repo:${OWNER}/${REPO}:ref:refs/heads/main" \
  "GitHub Actions deploy + eval-gate from main branch"

ensure_fc \
  "${APP_NAME}-pr" \
  "repo:${OWNER}/${REPO}:pull_request" \
  "GitHub Actions bicep what-if for pull requests"

# --- role assignment ---------------------------------------------------------

RG_SCOPE="/subscriptions/${AZD_SUB}/resourceGroups/${AZD_RG}"
EXISTING_ROLE="$(az role assignment list \
  --assignee "$SP_OBJECT_ID" \
  --role "$ROLE" \
  --scope "$RG_SCOPE" \
  --query '[0].id' -o tsv 2>/dev/null || true)"
if [ -n "$EXISTING_ROLE" ]; then
  log "role assignment '$ROLE' on $AZD_RG already exists — reusing"
else
  log "creating role assignment '$ROLE' for SP on $AZD_RG"
  az role assignment create \
    --assignee-object-id "$SP_OBJECT_ID" \
    --assignee-principal-type ServicePrincipal \
    --role "$ROLE" \
    --scope "$RG_SCOPE" >/dev/null
fi

# --- output -------------------------------------------------------------------

echo
log "bootstrap complete"
echo
cat <<EOF
Next: set the following GitHub repo variables (non-sensitive — variables, not secrets):

  gh variable set AZURE_CLIENT_ID       --body "$CLIENT_ID"
  gh variable set AZURE_TENANT_ID       --body "$TENANT_ID"
  gh variable set AZURE_SUBSCRIPTION_ID --body "$AZD_SUB"
  gh variable set AZURE_RESOURCE_GROUP  --body "$AZD_RG"
  gh variable set AZURE_LOCATION        --body "$AZD_LOC"

Verify with: gh variable list
EOF
