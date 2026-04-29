@description('Azure region for the Key Vault.')
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

@description('Tags applied to the Key Vault.')
param tags object

@description('Initial value for the JWT signing key. Defaults to a fresh GUID per deploy; rotate via az keyvault secret set after deployment — see README.')
@secure()
param jwtSigningKeyValue string = newGuid()

var keyVaultName = '${prefix}-${environmentName}-kv-${uniqueSuffix}'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enabledForDeployment: false
    enabledForDiskEncryption: false
    enabledForTemplateDeployment: false
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

resource jwtSigningKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'jwt-signing-key'
  properties: {
    value: jwtSigningKeyValue
    contentType: 'JWT signing key — rotate after deploy'
  }
}

output keyVaultId string = keyVault.id
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
output jwtSigningKeySecretName string = jwtSigningKey.name
