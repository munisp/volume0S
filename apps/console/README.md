# VolumeOS M1 Console

Responsive, installable operator console for the implemented M1 tenancy, commercial catalog, campaign-approval, Shopfront Studio QA, and outbound-safety contracts.

The unauthenticated console is explicitly a **product preview**. Its tenant, campaign, Studio, and offer records are illustrative. Business mutations are disabled until the required runtime APIs and an authenticated tenant session are available. Preview campaign and Studio records are removed when authentication succeeds; the console never enables workflow actions against them.

## Run locally

Requirements: Node.js 22.12+ and npm.

```bash
npm install
npm run dev
```

The development server listens on port 3000. Create a production build with:

```bash
npm run build
npm run lint
npm audit --audit-level=low
```

Serve `dist/` over HTTPS for installation and service-worker support outside localhost. The app uses relative asset URLs and does not require a client-side subroute fallback.

## Runtime configuration

Edit `public/runtime-config.json` before building, or replace `dist/runtime-config.json` at deployment time:

```json
{
  "coreApiUrl": "https://api.example.com",
  "orchestratorApiUrl": "https://workflow.example.com",
  "oidc": {
    "authority": "https://identity.example.com/realms/volumeos",
    "clientId": "volumeos-console",
    "scope": "openid profile email"
  }
}
```

Leave the orchestrator URL empty when that boundary is not deployed. Campaign and Studio mutations then remain disabled.

The runtime file is fetched with `cache: no-store` and is excluded from service-worker precaching. Do not put client secrets, provider credentials, suppression HMAC keys, or tenant records in this public file.

### OIDC and API requirements

- Register a public OIDC client for Authorization Code with PKCE; no client secret is embedded.
- Register the deployed console origin and path as the exact redirect and post-logout redirect URI.
- Issue RS256 access tokens accepted by the Core API, including its required audience and `tenant_id` claim.
- Restrict Core and orchestrator CORS to the console origin and required methods/headers.
- Configure Keycloak MFA and role assignments outside this frontend. Hiding or disabling a button is not an authorization boundary.
- The console stores OIDC state in tab-scoped `sessionStorage`; sign-out clears the local tab session. It does not claim to terminate the identity-provider session.

## API boundaries

Implemented Core calls:

- `GET /v1/offers`
- `GET` and `POST /v1/tenants/current`
- `POST /v1/workspaces`
- `POST /v1/subscriptions`
- `PUT /v1/tenants/current/outbound-suspension`
- `PUT` and `DELETE /v1/suppressions`

Implemented orchestrator calls:

- `GET` and `POST /v1/campaigns`
- `POST /v1/campaigns/{id}/approval`
- `GET` and `POST /v1/studio/packs`
- `GET /v1/studio/packs/{id}/assets`
- `POST /v1/studio/packs/{id}/review`

The console reads live campaign and Studio state from the orchestrator's Temporal-backed feeds, refreshes after mutations and while visible online, and disables actions when current state is unavailable. Studio review loads actual QA images through short-lived signed URLs, with an explicit refresh action for failed or expired URLs. Approval submits the current manifest SHA-256 only after every image loads; the service and workflow validate the same hash again before delivery. These controls do not bypass Temporal approval or provider policy checks, and source-level tests are not proof of a deployed integration.

## PWA and privacy behavior

- Static application assets, local fonts, manifest, and install icons are precached. `npm run icons` deterministically regenerates the reviewed PNG icons with a dependency-free Node script before development and production builds.
- `runtime-config.json`, API responses, tenant records, bearer tokens, and provider responses are not cached by the service worker.
- API calls use bearer authentication, `credentials: omit`, `cache: no-store`, JSON validation, and a 15-second timeout.
- Offline mode restores the application shell only; live operations remain network-dependent.
- Updates require an explicit reload prompt. Installation falls back to browser instructions when the native prompt is unavailable.

See [`docs/m1/CONSOLE.md`](../../docs/m1/CONSOLE.md) for acceptance evidence and remaining production gates.
