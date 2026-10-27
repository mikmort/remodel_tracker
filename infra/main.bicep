targetScope = 'resourceGroup'

param location string = resourceGroup().location

@minLength(3)
@maxLength(30)
param appName string

@minLength(3)
@maxLength(24)
param storageName string

@secure()
param ownerEmail string

@secure()
param ownerAadId string = ''

@secure()
param ownerGoogleId string = ''

param microsoftClientId string = ''

@secure()
param microsoftClientSecret string = ''

param googleClientId string = ''

@secure()
param googleClientSecret string = ''

var microsoftEnabled = !empty(microsoftClientId) && !empty(microsoftClientSecret)
var googleEnabled = !empty(googleClientId) && !empty(googleClientSecret)

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    isVersioningEnabled: true
    deleteRetentionPolicy: { enabled: true, days: 30 }
    containerDeleteRetentionPolicy: { enabled: true, days: 30 }
  }
}

resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'project'
  properties: { publicAccess: 'None' }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${appName}-plan'
  location: location
  kind: 'linux'
  sku: { name: 'B1', tier: 'Basic' }
  properties: { reserved: true }
}

resource site 'Microsoft.Web/sites@2023-12-01' = {
  name: appName
  location: location
  kind: 'app,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    clientAffinityEnabled: false
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
      http20Enabled: true
      appCommandLine: 'python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --no-access-log'
    }
  }
}

resource settings 'Microsoft.Web/sites/config@2023-12-01' = {
  parent: site
  name: 'appsettings'
  properties: {
    SCM_DO_BUILD_DURING_DEPLOYMENT: 'true'
    APP_ORIGIN: 'https://${site.properties.defaultHostName}'
    OWNER_EMAIL: ownerEmail
    OWNER_AAD_ID: ownerAadId
    OWNER_GOOGLE_ID: ownerGoogleId
    STORAGE_ACCOUNT_URL: storage.properties.primaryEndpoints.blob
    STORAGE_CONTAINER: container.name
    MICROSOFT_PROVIDER_AUTHENTICATION_SECRET: microsoftClientSecret
    GOOGLE_PROVIDER_AUTHENTICATION_SECRET: googleClientSecret
    MICROSOFT_LOGIN_ENABLED: string(microsoftEnabled)
    GOOGLE_LOGIN_ENABLED: string(googleEnabled)
  }
}

resource auth 'Microsoft.Web/sites/config@2023-12-01' = {
  parent: site
  name: 'authsettingsV2'
  dependsOn: [settings]
  properties: {
    platform: { enabled: true, runtimeVersion: '~1' }
    globalValidation: {
      requireAuthentication: true
      unauthenticatedClientAction: 'Return401'
      excludedPaths: ['/', '/static/*', '/health', '/auth-options']
    }
    httpSettings: { requireHttps: true }
    login: {
      tokenStore: { enabled: true }
      cookieExpiration: {
        convention: 'FixedTime'
        timeToExpiration: '08:00:00'
      }
    }
    identityProviders: {
      azureActiveDirectory: microsoftEnabled ? {
        enabled: true
        registration: {
          openIdIssuer: '${environment().authentication.loginEndpoint}consumers/v2.0'
          clientId: microsoftClientId
          clientSecretSettingName: 'MICROSOFT_PROVIDER_AUTHENTICATION_SECRET'
        }
        login: { loginParameters: ['scope=openid profile email'] }
        validation: { allowedAudiences: [microsoftClientId] }
      } : { enabled: false }
      google: googleEnabled ? {
        enabled: true
        registration: {
          clientId: googleClientId
          clientSecretSettingName: 'GOOGLE_PROVIDER_AUTHENTICATION_SECRET'
        }
        login: { scopes: ['openid', 'profile', 'email'] }
      } : { enabled: false }
    }
  }
}

resource contentRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(container.id, site.id, 'content-contributor')
  scope: container
  properties: {
    principalId: site.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}

output appUrl string = 'https://${site.properties.defaultHostName}'
output storageAccountUrl string = storage.properties.primaryEndpoints.blob
output contentContainerId string = container.id
output appPrincipalId string = site.identity.principalId
