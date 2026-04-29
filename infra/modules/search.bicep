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

var searchName = '${prefix}-${environmentName}-search-${uniqueSuffix}'

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

output searchId string = search.id
output searchName string = search.name
output searchEndpoint string = 'https://${search.name}.search.windows.net'
