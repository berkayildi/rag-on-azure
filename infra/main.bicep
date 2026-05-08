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

@description('Container image to deploy. Day 5 swaps from the MCR placeholder to the GHCR image built by .github/workflows/build-image.yml. Day 7 CI will pin to immutable sha-<short> tags; latest-dev is correct for the v0.1 manual deploy loop.')
param containerImage string = 'ghcr.io/berkayildi/rag-on-azure:latest-dev'

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
