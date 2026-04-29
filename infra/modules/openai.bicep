@description('Azure region for the Azure OpenAI account.')
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

@description('Tags applied to the OpenAI account.')
param tags object

@description('Embedding model deployment capacity in 1k-TPM units.')
@minValue(1)
@maxValue(120)
param embeddingCapacity int = 30

@description('Chat model deployment capacity in 1k-TPM units.')
@minValue(1)
@maxValue(120)
param chatCapacity int = 50

@description('Deployment SKU for the embedding model. Defaults to DataZoneStandard because Sweden Central does not offer regional Standard for text-embedding-3-small.')
@allowed([
  'Standard'
  'DataZoneStandard'
  'GlobalStandard'
])
param embeddingSku string = 'DataZoneStandard'

@description('Deployment SKU for the chat model. Defaults to Standard (regional, fully in Sweden Central).')
@allowed([
  'Standard'
  'DataZoneStandard'
  'GlobalStandard'
])
param chatSku string = 'Standard'

@description('Embedding model name.')
param embeddingModelName string = 'text-embedding-3-small'

@description('Embedding model version.')
param embeddingModelVersion string = '1'

@description('Chat model name. Defaults to gpt-4o because gpt-4o-mini@2024-07-18 stopped accepting new deployments on 2026-03-31; see design spec §0 for the chain of forcing functions.')
param chatModelName string = 'gpt-4o'

@description('Chat model version.')
param chatModelVersion string = '2024-08-06'

var openaiName = '${prefix}-${environmentName}-openai-${uniqueSuffix}'
var embeddingDeploymentName = 'embedding'
var chatDeploymentName = 'chat'

resource openai 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: openaiName
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: openaiName
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
    disableLocalAuth: true
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openai
  name: embeddingDeploymentName
  sku: {
    name: embeddingSku
    capacity: embeddingCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: embeddingModelName
      version: embeddingModelVersion
    }
  }
}

resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openai
  name: chatDeploymentName
  sku: {
    name: chatSku
    capacity: chatCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: chatModelName
      version: chatModelVersion
    }
  }
  dependsOn: [
    embeddingDeployment
  ]
}

output openaiId string = openai.id
output openaiName string = openai.name
output openaiEndpoint string = openai.properties.endpoint
output embeddingDeploymentName string = embeddingDeployment.name
output chatDeploymentName string = chatDeployment.name
