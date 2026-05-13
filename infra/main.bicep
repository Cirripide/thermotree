// infra/main.bicep
// Resource-group-scoped Bicep template that provisions everything Thermotree
// needs in Azure: Log Analytics, ACR, a Container Apps Environment, and the
// two Container Apps (backend internal, frontend external).
//
// The resource group must already exist:
//   az group create -n rg-thermotree -l westeurope
//
// Run from the pipeline (Infra stage) with:
//   az deployment group create -g rg-thermotree \
//     -f infra/main.bicep -p infra/main.parameters.json \
//     -p acrName=$(ACR_NAME) \
//     -p nominatimUserAgent="$(NOMINATIM_USER_AGENT)" \
//     -p photonUserAgent="$(PHOTON_USER_AGENT)"

@description('Azure region. Must match the resource group region.')
param location string = resourceGroup().location

@description('Globally unique ACR name (5-50 chars, lowercase alphanumeric).')
@minLength(5)
@maxLength(50)
param acrName string

@description('Log Analytics workspace name.')
param logWorkspaceName string = 'log-thermotree'

@description('Container Apps Environment name.')
param environmentName string = 'cae-thermotree'

@description('Backend Container App name. The internal FQDN derives from this: <name>.internal.<env defaultDomain>.')
param backendAppName string = 'ca-backend'

@description('Frontend Container App name.')
param frontendAppName string = 'ca-frontend'

@description('Placeholder image. Kept as a sensible default for `backendImage` / `frontendImage` when running Bicep locally, but the workflow never deploys Container Apps with this image — it skips the apps on the foundation pass and supplies real images on the apps pass.')
param placeholderImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Skip Container App + AcrPull role assignments. Set false on the foundation pass (before images are built), true on the apps pass.')
param deployContainerApps bool = true

@description('Full backend image reference (registry/repo:tag). Default = placeholder; the workflow overrides this on the apps pass with the SHA-tagged image just pushed to ACR.')
param backendImage string = placeholderImage

@description('Full frontend image reference. Same contract as backendImage.')
param frontendImage string = placeholderImage

@description('UVICORN_WORKERS for the backend. DO NOT increase: ZonesRunner concurrency cap and coalescing are per-process invariants in backend/app/core/config.py.')
param uvicornWorkers string = '1'

@description('Comma-separated STAC providers. Empty = backend default (planetary_computer,element84).')
param stacProviders string = ''

@description('Nominatim User-Agent. MUST be a descriptive UA with contact email per Nominatim policy.')
@secure()
param nominatimUserAgent string

@description('Photon User-Agent. MUST be a descriptive UA with contact email per Photon fair-use policy.')
@secure()
param photonUserAgent string

@description('LocationIQ API key. Empty = use public Photon/Nominatim fallback. Non-empty = activate LocationIQGeocoder.')
@secure()
param locationIqApiKey string = ''

@description('Log retention in days for the Log Analytics workspace.')
param logRetentionDays int = 30

// ---------------------------------------------------------------------------
// Log Analytics workspace
// ---------------------------------------------------------------------------
resource logws 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logWorkspaceName
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: logRetentionDays
    features: { enableLogAccessUsingOnlyResourcePermissions: true }
  }
}

// ---------------------------------------------------------------------------
// Azure Container Registry
//
// Basic SKU is fine for POC. adminUserEnabled:false forces AAD auth — both
// Container Apps pull via system-assigned managed identity (AcrPull role
// assignments below). publicNetworkAccess stays enabled until we move to
// a private endpoint, which is deferred.
// ---------------------------------------------------------------------------
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
    zoneRedundancy: 'Disabled'
  }
}

// ---------------------------------------------------------------------------
// Container Apps Environment (Consumption profile, no VNet)
// ---------------------------------------------------------------------------
resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logws.properties.customerId
        sharedKey: logws.listKeys().primarySharedKey
      }
    }
    zoneRedundant: false
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Backend Container App — internal ingress
// ---------------------------------------------------------------------------
resource backend 'Microsoft.App/containerApps@2024-03-01' = if (deployContainerApps) {
  name: backendAppName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: cae.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: 'system'
        }
      ]
      secrets: concat([
        {
          name: 'nominatim-user-agent'
          value: nominatimUserAgent
        }
        {
          name: 'photon-user-agent'
          value: photonUserAgent
        }
      ], empty(locationIqApiKey) ? [] : [
        {
          name: 'locationiq-api-key'
          value: locationIqApiKey
        }
      ])
    }
    template: {
      containers: [
        {
          name: 'backend'
          image: backendImage
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: concat([
            { name: 'NOMINATIM_USER_AGENT', secretRef: 'nominatim-user-agent' }
            { name: 'PHOTON_USER_AGENT', secretRef: 'photon-user-agent' }
            { name: 'UVICORN_WORKERS', value: uvicornWorkers }
            { name: 'PORT', value: '8000' }
          ], empty(stacProviders) ? [] : [
            { name: 'STAC_PROVIDERS', value: stacProviders }
          ], empty(locationIqApiKey) ? [] : [
            { name: 'LOCATIONIQ_API_KEY', secretRef: 'locationiq-api-key' }
          ])
          probes: [
            {
              type: 'Startup'
              httpGet: {
                path: '/'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 5
              failureThreshold: 30
              timeoutSeconds: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/'
                port: 8000
              }
              periodSeconds: 10
              failureThreshold: 3
              timeoutSeconds: 3
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/'
                port: 8000
              }
              periodSeconds: 30
              failureThreshold: 3
              timeoutSeconds: 5
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 3
        rules: [
          {
            name: 'http-concurrency'
            http: {
              metadata: {
                concurrentRequests: '4'
              }
            }
          }
        ]
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Frontend Container App — external ingress
//
// The UPSTREAM_BACKEND env var is composed from the backend app name and
// the environment's defaultDomain. The .internal subdomain is only
// resolvable from inside the same Container Apps Environment.
// ---------------------------------------------------------------------------
resource frontend 'Microsoft.App/containerApps@2024-03-01' = if (deployContainerApps) {
  name: frontendAppName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: cae.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'frontend'
          image: frontendImage
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            {
              name: 'UPSTREAM_BACKEND'
              value: '${backend.name}.internal.${cae.properties.defaultDomain}'
            }
            {
              name: 'UPSTREAM_SCHEME'
              value: 'https'
            }
          ]
          probes: [
            {
              type: 'Startup'
              httpGet: {
                path: '/'
                port: 8080
              }
              initialDelaySeconds: 2
              periodSeconds: 3
              failureThreshold: 10
              timeoutSeconds: 2
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/'
                port: 8080
              }
              periodSeconds: 10
              failureThreshold: 3
              timeoutSeconds: 2
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/'
                port: 8080
              }
              periodSeconds: 30
              failureThreshold: 3
              timeoutSeconds: 5
            }
          ]
        }
      ]
      scale: {
        // 1 minimum so the first visitor never sees a cold start; nginx
        // is cheap and the always-on cost is negligible.
        minReplicas: 1
        maxReplicas: 3
        rules: [
          {
            name: 'http-concurrency'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
}

// ---------------------------------------------------------------------------
// AcrPull role assignments — both container apps need to pull from ACR
// via their system-assigned managed identity. principalId is available
// inside the same deployment in API versions 2023-05-01+; Bicep emits an
// implicit dependency on each container app via the reference.
//
// Deterministic GUID names per (scope, role, principal) so reruns are
// idempotent — same name produces a no-op update, not a duplicate.
// ---------------------------------------------------------------------------
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource acrPullBackend 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployContainerApps) {
  name: guid(acr.id, backendAppName, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: backend.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource acrPullFrontend 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployContainerApps) {
  name: guid(acr.id, frontendAppName, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: frontend.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Outputs consumed by the pipeline Build / Deploy stages.
// ---------------------------------------------------------------------------
output acrLoginServer string = acr.properties.loginServer
output acrName string = acr.name
output envDefaultDomain string = cae.properties.defaultDomain
output backendAppName string = deployContainerApps ? backend.name : ''
output frontendAppName string = deployContainerApps ? frontend.name : ''
output frontendFqdn string = deployContainerApps ? frontend.properties.configuration.ingress.fqdn : ''
output backendInternalFqdn string = deployContainerApps ? '${backend.name}.internal.${cae.properties.defaultDomain}' : ''
