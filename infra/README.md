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

The placeholder defaults in `backend/app/core/config.py` technically
work but violate both providers' fair-use policies — use a real contact
email in the user-agent secrets.

### 4. First workflow run

The first run is automatic — push to `main` (or merge a PR). Track it
at `Actions → Azure deploy`. The first run takes ~10–15 minutes (Bicep
does heavy infra creation, ACR Basic seeds slowly). Subsequent runs are
~3–5 min thanks to registry-side build cache.

## First-run footguns

| Symptom | Cause | Fix |
|---|---|---|
| `ResourceGroupNotFound` | RG not created | Run step 1 (`az group create`). |
| `RegistryNameInUse` | ACR name taken globally | Bump suffix in `infra/main.parameters.json` AND the `ACR_NAME` repository variable. |
| `AuthorizationFailed` on role assignment | SP lacks UAA on the RG | Re-run the UAA role grant in step 2. |
| `AADSTS70021` on `azure/login` step | Federated credential subject doesn't match | The subject must be `repo:<ORG>/<REPO>:ref:refs/heads/main` (push) or `repo:<ORG>/<REPO>:pull_request` (PRs) — case-sensitive. |
| `QuotaExceeded` in westeurope | Region capacity | Switch `location` in `infra/main.parameters.json` and `az group create -l` to `northeurope`. |
| Both Container Apps show `Activation failed` after Infra stage | Expected. Placeholder image `mcr.microsoft.com/k8se/quickstart` listens on port 80, our `targetPort` is 8000/8080 → probes fail. | Wait for Build + Deploy to flip both apps to the real images. Single-mode revisions auto-retire the failed placeholder revision once a healthy one exists. |

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
