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

@description('Chat model version. 2024-11-20 is the only currently-deployable gpt-4o variant on this subscription; @2024-08-06 is rejected by the validator due to a dual SKU listing with one expired sunset. See design spec §0.')
param chatModelVersion string = '2024-11-20'

@description('Optional. Object ID of a developer principal (e.g. `az ad signed-in-user show --query id -o tsv`) to grant Cognitive Services OpenAI User on this account. Empty string disables the assignment. Used so a human running `make ingest` locally can call the embedding deployment via DefaultAzureCredential. The deployed Container App MI already has the same role assignment in `modules/containerapp.bicep`. Mirrors the search-service developer RBAC pattern.')
param developerPrincipalId string = ''

var openaiName = '${prefix}-${environmentName}-openai-${uniqueSuffix}'
var embeddingDeploymentName = 'embedding'
var chatDeploymentName = 'chat'

var cognitiveServicesOpenAIUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

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

resource developerOpenAIUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(developerPrincipalId)) {
  name: guid(openai.id, developerPrincipalId, cognitiveServicesOpenAIUserRoleId)
  scope: openai
  properties: {
    principalId: developerPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUserRoleId)
  }
}

output openaiId string = openai.id
output openaiName string = openai.name
output openaiEndpoint string = openai.properties.endpoint
output embeddingDeploymentName string = embeddingDeployment.name
output chatDeploymentName string = chatDeployment.name
