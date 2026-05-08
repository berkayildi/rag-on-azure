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

// Secret values are operator territory. Bicep provisions the vault and RBAC
// only; secrets like `jwt-signing-key` are populated and rotated out-of-band
// via `az keyvault secret set` — see AGENTS.md §"Operational quirks" for the
// one-time public-PEM upload procedure. Keeping the secret out of Bicep
// prevents every deploy from overwriting a rotated key with a fresh GUID.

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

output keyVaultId string = keyVault.id
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
