targetScope = 'resourceGroup'

@description('Azure region. Defaults to swedencentral — see design spec §0 for region selection.')
param location string = 'swedencentral'

@description('Environment name.')
@allowed([
  'dev'
  'prod'
])
param environmentName string

@description('Resource name prefix.')
@minLength(2)
@maxLength(8)
param prefix string = 'rag'

@description('AI Search SKU. Free for dev; Basic for production-grade workloads.')
@allowed([
  'free'
  'basic'
])
param searchSku string = 'free'

@description('Tenant IDs to seed. Reserved for the ingest pipeline; not consumed by infra in v0.1. Surfaced as an output so downstream tooling can read it back.')
param tenantSeedIds array = []

@description('Container image to deploy. REQUIRED — no default. Every deploy path MUST pass an immutable sha-<short> tag (e.g. `ghcr.io/berkayildi/rag-on-azure:sha-abc1234`) via `--parameters containerImage=...`. CI\'s deploy + bicep-whatif jobs in `.github/workflows/ci.yml` already do this; local `make plan/apply` must too, or Bicep refuses the deployment at parameter validation. Closes the AGENTS.md operational quirk where the prior `latest-dev` default would silently regress the running revision on a local apply. Bicep `assert` would have been ideal but is still experimental (BCP349); a required param achieves the same fail-at-validation guarantee with no preview-feature dependency.')
param containerImage string

@description('Optional. Object ID of a developer principal granted Search Index Data Contributor on the search service so a human can run `make ingest` locally. Set this in the gitignored `main.parameters.json` only — never commit a real GUID. See README §Development.')
param developerPrincipalId string = ''

@description('Optional. Object ID of the OIDC-federated CI service principal granted Search Index Data Reader on the search service so the eval-gate job can snapshot the index. Passed in CI from `vars.AZURE_CI_PRINCIPAL_ID`; left empty for local deploys.')
param ciPrincipalId string = ''

var uniqueSuffix = take(uniqueString(resourceGroup().id), 6)

var tags = {
  project: 'rag-on-azure'
  environment: environmentName
  managedBy: 'bicep'
}

module monitor 'modules/monitor.bicep' = {
  name: 'monitor'
  params: {
    location: location
    prefix: prefix
    environmentName: environmentName
    tags: tags
  }
}

module keyvault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  params: {
    location: location
    prefix: prefix
    environmentName: environmentName
    uniqueSuffix: uniqueSuffix
    tags: tags
  }
}

module search 'modules/search.bicep' = {
  name: 'search'
  params: {
    location: location
    prefix: prefix
    environmentName: environmentName
    uniqueSuffix: uniqueSuffix
    sku: searchSku
    tags: tags
    developerPrincipalId: developerPrincipalId
    ciPrincipalId: ciPrincipalId
  }
}

module openai 'modules/openai.bicep' = {
  name: 'openai'
  params: {
    location: location
    prefix: prefix
    environmentName: environmentName
    uniqueSuffix: uniqueSuffix
    tags: tags
    developerPrincipalId: developerPrincipalId
  }
}

module containerapp 'modules/containerapp.bicep' = {
  name: 'containerapp'
  params: {
    location: location
    prefix: prefix
    environmentName: environmentName
    tags: tags
    logAnalyticsWorkspaceName: monitor.outputs.workspaceName
    searchName: search.outputs.searchName
    searchEndpoint: search.outputs.searchEndpoint
    openaiName: openai.outputs.openaiName
    openaiEndpoint: openai.outputs.openaiEndpoint
    embeddingDeploymentName: openai.outputs.embeddingDeploymentName
    chatDeploymentName: openai.outputs.chatDeploymentName
    keyVaultName: keyvault.outputs.keyVaultName
    keyVaultUri: keyvault.outputs.keyVaultUri
    containerImage: containerImage
  }
}

output containerAppFqdn string = containerapp.outputs.containerAppFqdn
output searchEndpoint string = search.outputs.searchEndpoint
output openaiEndpoint string = openai.outputs.openaiEndpoint
output keyVaultName string = keyvault.outputs.keyVaultName
output tenantSeedIds array = tenantSeedIds
