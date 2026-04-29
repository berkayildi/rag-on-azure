targetScope = 'resourceGroup'

@description('Azure region. Defaults to the resource group location.')
param location string = resourceGroup().location

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

@description('Initial JWT signing key value. Defaults to a fresh GUID per deploy; rotate via az keyvault secret set after deployment — see README.')
@secure()
param jwtSigningKeyValue string = newGuid()

@description('Container image to deploy. Day 2 uses an MCR placeholder; the FastAPI image lands in CI from Day 5 onwards.')
param containerImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

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
    jwtSigningKeyValue: jwtSigningKeyValue
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
