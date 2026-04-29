@description('Azure region for the Container App and its environment.')
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

@description('Tags applied to the environment and the Container App.')
param tags object

@description('Name of the Log Analytics workspace to bind for environment logs.')
param logAnalyticsWorkspaceName string

@description('Name of the Azure AI Search service the app reads from.')
param searchName string

@description('Endpoint of the Azure AI Search service.')
param searchEndpoint string

@description('Name of the Azure OpenAI account.')
param openaiName string

@description('Endpoint of the Azure OpenAI account.')
param openaiEndpoint string

@description('Embedding model deployment name on the OpenAI account.')
param embeddingDeploymentName string

@description('Chat model deployment name on the OpenAI account.')
param chatDeploymentName string

@description('Name of the Key Vault.')
param keyVaultName string

@description('URI of the Key Vault.')
param keyVaultUri string

@description('Logical name of the JWT signing key secret in Key Vault.')
param jwtSigningKeySecretName string = 'jwt-signing-key'

@description('Container image. Day 2 uses an MCR placeholder; the FastAPI image lands in CI from Day 5 onwards.')
param containerImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Minimum replica count. MUST stay at 0 in dev to preserve scale-to-zero.')
@minValue(0)
@maxValue(25)
param minReplicas int = 0

@description('Maximum replica count. Hard cap to bound denial-of-wallet risk.')
@minValue(1)
@maxValue(25)
param maxReplicas int = 3

@description('Ingress target port. Matches the FastAPI service that lands on Day 5.')
param ingressTargetPort int = 8000

@description('Concurrency threshold per replica for the HTTP scale rule.')
@minValue(1)
@maxValue(1000)
param httpConcurrentRequests int = 50

var environmentResourceName = '${prefix}-${environmentName}-cae'
var containerAppName = '${prefix}-${environmentName}-ca'

var cognitiveServicesOpenAIUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var searchIndexDataReaderRoleId = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
var searchIndexDataContributorRoleId = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsWorkspaceName
}

resource search 'Microsoft.Search/searchServices@2023-11-01' existing = {
  name: searchName
}

resource openai 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: openaiName
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentResourceName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  tags: union(tags, {
    'azd-service-name': 'app'
  })
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: managedEnvironment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: ingressTargetPort
        transport: 'auto'
        allowInsecure: false
      }
    }
    template: {
      containers: [
        {
          name: 'app'
          image: containerImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'AZURE_SEARCH_ENDPOINT'
              value: searchEndpoint
            }
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: openaiEndpoint
            }
            {
              name: 'AZURE_OPENAI_CHAT_DEPLOYMENT'
              value: chatDeploymentName
            }
            {
              name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT'
              value: embeddingDeploymentName
            }
            {
              name: 'KEY_VAULT_URI'
              value: keyVaultUri
            }
            {
              name: 'JWT_SIGNING_KEY_REF'
              value: '${keyVaultUri}secrets/${jwtSigningKeySecretName}'
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-concurrency'
            http: {
              metadata: {
                concurrentRequests: string(httpConcurrentRequests)
              }
            }
          }
        ]
      }
    }
  }
}

resource openaiUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openai.id, containerApp.id, cognitiveServicesOpenAIUserRoleId)
  scope: openai
  properties: {
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUserRoleId)
  }
}

resource searchReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, containerApp.id, searchIndexDataReaderRoleId)
  scope: search
  properties: {
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataReaderRoleId)
  }
}

resource searchContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, containerApp.id, searchIndexDataContributorRoleId)
  scope: search
  properties: {
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributorRoleId)
  }
}

resource keyVaultSecretsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, containerApp.id, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
  }
}

output containerAppId string = containerApp.id
output containerAppName string = containerApp.name
output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output managedEnvironmentId string = managedEnvironment.id
output managedEnvironmentName string = managedEnvironment.name
output principalId string = containerApp.identity.principalId
