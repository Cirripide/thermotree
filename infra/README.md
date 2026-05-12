# Thermotree — Azure deployment

The workflow at `/.github/workflows/azure-deploy.yml` ships Thermotree to
Azure Container Apps. The trigger lives in GitHub Actions; the target
environment lives in Azure. This document covers the **one-time manual
setup** the workflow depends on. After it's done, every push to `main`
runs Validate → Infra → Build → Deploy automatically.

## Topology

```
rg-thermotree (westeurope, manually created)
 ├ log-thermotree            Log Analytics workspace
 ├ crthermotreepoc01         ACR Basic, admin disabled
 ├ cae-thermotree            Container Apps Environment (Consumption)
 ├ ca-backend                internal ingress :8000, scale 0–3
 └ ca-frontend               external ingress :8080, scale 1–3
```

Frontend is the only public endpoint. Backend is reachable only at
`ca-backend.internal.<env-default-domain>:8000`, resolved per-request
through the Azure DNS resolver wired into `frontend/nginx.conf.template`.

## One-time prerequisites

### 1. Azure side

```bash
# Pin subscription.
az login
az account set --subscription <SUB_ID>

# Resource group. Bicep is RG-scoped and will not create this.
az group create -n rg-thermotree -l westeurope

# Confirm the ACR name is still globally available. If not, bump the
# suffix in infra/main.parameters.json (acrName) AND the GitHub
# repository variable ACR_NAME — they must match.
az acr check-name --name crthermotreepoc01
```

### 2. Azure AD app registration with GitHub OIDC

Create (or reuse) an Azure AD app registration whose service principal
the GitHub workflow will assume. Auth is **OIDC federated**, so no
client secret is ever stored on the GitHub side.

```bash
# Create an app registration + service principal scoped to the RG.
sp_json=$(az ad sp create-for-rbac \
  --name thermotree-github \
  --role "Contributor" \
  --scopes "/subscriptions/<SUB_ID>/resourceGroups/rg-thermotree" \
  --json-auth)
client_id=$(echo "$sp_json" | jq -r .clientId)
tenant_id=$(echo "$sp_json" | jq -r .tenantId)

# Bicep performs role assignments (AcrPull from each Container App's
# managed identity to the ACR), so the SP also needs UAA on the RG.
az role assignment create \
  --assignee "$client_id" \
  --role "User Access Administrator" \
  --scope "/subscriptions/<SUB_ID>/resourceGroups/rg-thermotree"

# Federated credential #1 — push to main (Infra/Build/Deploy stages).
az ad app federated-credential create \
  --id "$client_id" \
  --parameters '{
    "name": "github-main",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:<ORG>/<REPO>:ref:refs/heads/main",
    "audiences": ["api://AzureADTokenExchange"]
  }'

# Federated credential #2 — pull_request (Validate stage what-if).
az ad app federated-credential create \
  --id "$client_id" \
  --parameters '{
    "name": "github-pr",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:<ORG>/<REPO>:pull_request",
    "audiences": ["api://AzureADTokenExchange"]
  }'

echo "AZURE_CLIENT_ID=$client_id"
echo "AZURE_TENANT_ID=$tenant_id"
```

Substitute `<ORG>/<REPO>` with the actual GitHub `owner/name` — the
subject claim is case-sensitive and must match GitHub exactly.

### 3. GitHub repository configuration

In `Settings → Secrets and variables → Actions`:

**Repository secrets** (encrypted, never echoed in logs):

| Secret | Value |
|---|---|
| `AZURE_CLIENT_ID`        | output of step 2 |
| `AZURE_TENANT_ID`        | output of step 2 |
| `AZURE_SUBSCRIPTION_ID`  | subscription holding `rg-thermotree` |
| `NOMINATIM_USER_AGENT`   | e.g. `thermotree/0.1 (contact: you@example.com)` |
| `PHOTON_USER_AGENT`      | e.g. `thermotree/0.1 (contact: you@example.com)` |
| `LOCATIONIQ_API_KEY`     | LocationIQ access token (e.g. `pk.xxxx`). Leave unset to keep the Photon/Nominatim fallback. |

When `LOCATIONIQ_API_KEY` is set, the backend uses LocationIQ for
geocoding and boundary lookups. When unset/empty, it falls back to the
public Photon + Nominatim composite (Bicep conditionally omits the
secret + env var entirely so ACA doesn't choke on empty values). See
`backend/app/core/config.py` for the full set of LocationIQ-related env
vars (`LOCATIONIQ_BASE_URL`, `LOCATIONIQ_USER_AGENT`) — both have
sensible defaults and don't need to be set in GitHub.

**Repository variables** (visible in logs, non-sensitive):

| Variable | Value |
|---|---|
| `ACR_NAME` | `crthermotreepoc01` |
| `PUBLIC_API_URL` (optional) | `/api/` (default — leave unset). Set to an absolute URL like `https://api.thermotree.com/api/` only after completing the prerequisites in "Switching to an absolute `PUBLIC_API_URL`" below. |

The placeholder defaults in `backend/app/core/config.py` technically
work but violate both providers' fair-use policies — use a real contact
email in the user-agent secrets.

### Switching to an absolute `PUBLIC_API_URL`

Default behavior: the Angular app uses the relative URL `/api/` and the
frontend container's nginx (`frontend/nginx.conf.template`) reverse-proxies
it to the backend's internal FQDN. Single origin, no CORS, backend stays
private. **Don't change `PUBLIC_API_URL` unless you also do all three of
the following** — otherwise the browser will start hitting a backend host
that's either unreachable or rejecting its requests:

1. **Make the backend publicly reachable.** Flip `infra/main.bicep` line ~130
   from `external: false` to `external: true`, redeploy, then bind a custom
   hostname + managed cert to `ca-backend` (`az containerapp hostname add`).
2. **Enable CORS in the backend.** Add `CORSMiddleware` to `backend/app/main.py`
   with the frontend's exact origin (e.g. `https://www.thermotree.com`) in
   `allow_origins`.
3. **DNS.** Point the chosen backend hostname (e.g. `api.thermotree.com`) at
   the backend's external FQDN via CNAME.

Then set `PUBLIC_API_URL` in `Settings → Variables → Actions` to the full
absolute URL (with trailing slash, matching the existing literal:
`https://api.thermotree.com/api/`) and re-run the workflow. The
`build-frontend` job bakes the new value into `environment.ts` at build
time.

### 4. First workflow run

The first run is automatic — push to `main` (or merge a PR). Track it
at `Actions → Azure deploy`. The first run takes ~10–15 minutes (Bicep
does heavy infra creation, ACR Basic seeds slowly). Subsequent runs are
~3–5 min thanks to registry-side build cache.

## Deploy flow

The workflow runs Bicep twice per push, in a two-pass shape that
prevents the historical "Container App can't come up because the
placeholder image is on the wrong port" race:

1. **Foundation pass** (`infra-foundation` job): Bicep with
   `deployContainerApps=false` provisions ACR + Container Apps
   Environment + Log Analytics only.
2. **Build** (`build-backend`, `build-frontend` jobs, parallel): push
   SHA-tagged images to ACR.
3. **Apps pass** (`infra-apps` job): Bicep with
   `deployContainerApps=true` and `backendImage`/`frontendImage`
   pointing at the freshly-built images creates the two Container Apps
   directly with the real images. Revisions go Healthy on the first
   try — no placeholder phase to wait through.

## First-run footguns

| Symptom | Cause | Fix |
|---|---|---|
| `ResourceGroupNotFound` | RG not created | Run step 1 (`az group create`). |
| `RegistryNameInUse` | ACR name taken globally | Bump suffix in `infra/main.parameters.json` AND the `ACR_NAME` repository variable. |
| `AuthorizationFailed` on role assignment | SP lacks UAA on the RG | Re-run the UAA role grant in step 2. |
| `AADSTS70021` on `azure/login` step | Federated credential subject doesn't match | The subject must be `repo:<ORG>/<REPO>:ref:refs/heads/main` (push) or `repo:<ORG>/<REPO>:pull_request` (PRs) — case-sensitive. |
| `QuotaExceeded` in westeurope | Region capacity | Switch `location` in `infra/main.parameters.json` and `az group create -l` to `northeurope`. |
| `Failed to provision revision ... Operation expired` on `infra-apps` | A previous failed run left a Container App stuck in `Failed` provisioning state | `az containerapp delete -g rg-thermotree -n ca-backend --yes && az containerapp delete -g rg-thermotree -n ca-frontend --yes`, then re-run the workflow. |

## Verification

After the first successful Deploy stage:

1. Find the frontend FQDN in the Deploy log (`Frontend FQDN:
   https://...`) or via `az containerapp show -g rg-thermotree -n
   ca-frontend --query properties.configuration.ingress.fqdn -o tsv`.
2. `curl -fsS https://<fqdn>/` → SPA HTML.
3. `curl -fsS 'https://<fqdn>/api/geocode/search?q=milano&limit=3'` →
   JSON list (proves nginx → backend reverse proxy works through ACA's
   internal DNS).
4. Open `https://<fqdn>` in a browser, pick a small Northern-hemisphere
   city (e.g. Lugano), confirm the boundary loads and the swipe map
   renders both LST and NDVI for Summer 2023.

## Out of scope

The workflow assumes a single environment. Add a manual approval gate
(via a GitHub `production` Environment with required reviewers),
strict Bicep lint (`--diagnostics-as-errors`), private endpoints,
Key Vault references for secrets, and a second stage (staging + prod)
in follow-up turns.
