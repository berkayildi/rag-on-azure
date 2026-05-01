@description('Azure region for the search service.')
param location string

@description('Resource name prefix.')
@minLength(2)
@maxLength(8)
param prefix string

@description('Environment name.')
@allowed([
  'dev'
  'prod'
])
param environmentName string

@description('Six-character uniqueness suffix derived from the resource group ID.')
@minLength(4)
@maxLength(8)
param uniqueSuffix string

@description('AI Search SKU. Free for dev; Basic for production-grade workloads.')
@allowed([
  'free'
  'basic'
])
param sku string = 'free'

@description('Tags applied to the search service.')
param tags object

@description('Optional. Object ID of a developer principal (e.g. `az ad signed-in-user show --query id -o tsv`) to grant Search Index Data Contributor on this service. Empty string disables the assignment. Used so a human running `make ingest` locally can create indexes against this service. The deployed Container App MI already has the same role assignment in `modules/containerapp.bicep`.')
param developerPrincipalId string = ''

var searchName = '${prefix}-${environmentName}-search-${uniqueSuffix}'

var searchIndexDataContributorRoleId = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: searchName
  location: location
  tags: tags
  sku: {
    name: sku
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'enabled'
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
    semanticSearch: 'disabled'
  }
}

resource developerSearchContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(developerPrincipalId)) {
  name: guid(search.id, developerPrincipalId, searchIndexDataContributorRoleId)
  scope: search
  properties: {
    principalId: developerPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributorRoleId)
  }
}

output searchId string = search.id
output searchName string = search.name
output searchEndpoint string = 'https://${search.name}.search.windows.net'
