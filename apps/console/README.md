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

Configurable orchestrator calls:

- `POST /v1/campaigns`
- `POST /v1/campaigns/{id}/approval`
- `POST /v1/studio/packs`
- `POST /v1/studio/packs/{id}/review`

The orchestrator HTTP service, live campaign/Studio queue feeds, and actual QA object-delivery interface are not implemented by this static console. Submitted workflow records appear only in the current tab and are labeled as not refreshable. Actual image review must use the deployed asset interface before QA approval. These boundaries do not bypass Temporal approval or provider policy checks.

## PWA and privacy behavior

- Static application assets, local fonts, manifest, and install icons are precached. `npm run icons` deterministically regenerates the reviewed PNG icons with a dependency-free Node script before development and production builds.
- `runtime-config.json`, API responses, tenant records, bearer tokens, and provider responses are not cached by the service worker.
- API calls use bearer authentication, `credentials: omit`, `cache: no-store`, JSON validation, and a 15-second timeout.
- Offline mode restores the application shell only; live operations remain network-dependent.
- Updates require an explicit reload prompt. Installation falls back to browser instructions when the native prompt is unavailable.

See [`docs/m1/CONSOLE.md`](../../docs/m1/CONSOLE.md) for acceptance evidence and remaining production gates.
