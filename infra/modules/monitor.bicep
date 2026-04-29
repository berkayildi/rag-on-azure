@description('Azure region for monitor resources.')
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

@description('Tags applied to every resource.')
param tags object

@description('Log Analytics workspace retention in days.')
@minValue(30)
@maxValue(730)
param retentionInDays int = 30

var workspaceName = '${prefix}-${environmentName}-law'
var appInsightsName = '${prefix}-${environmentName}-appi'

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionInDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

output workspaceId string = workspace.id
output workspaceName string = workspace.name
output appInsightsId string = appInsights.id
output appInsightsName string = appInsights.name
