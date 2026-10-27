# M1 Console Delivery and Verification

Status: the responsive M1 PWA, orchestrator HTTP service, live Temporal queue feeds, and object-backed Studio review are implemented in source and covered by service and synthetic-browser tests. Production OIDC/CORS, deployed API authorization, production images, and staging acceptance remain operational gates.

## Delivered scope

- Responsive Overview, Onboarding, Campaigns, Studio QA, Offers, and Settings & safety screens.
- Desktop navigation rail, mobile navigation, safe-area spacing, visible focus states, reduced-motion handling, and keyboard-contained dialogs with trigger-focus restoration.
- Installable standalone PWA with local fonts/icons, static-shell offline reload, update prompt, and browser-install fallback.
- OIDC Authorization Code with PKCE and tab-scoped session storage; no client secret.
- Real Core API contracts for catalog, tenant, workspace, subscription, outbound suspension, and recipient suppression.
- Tenant-scoped orchestrator service with RS256 OIDC authentication, role checks, real Temporal workflow start/query/list/signal operations, and no parallel queue store.
- Live campaign and Studio feeds refreshed on connection, every 30 seconds while visible and online, on demand, and after mutations. Feed failures disable human actions until current state loads.
- Object-backed Studio QA gallery using short-lived presigned URLs. Approval requires the exact current manifest hash and successful loading of every image; rejection requires a reason and the current manifest but remains available when an image fails. A dedicated refresh action revalidates the manifest and replaces failed or expired signed URLs.
- Explicit preview/live separation. Authenticated sessions discard all illustrative campaign and Studio records rather than permitting actions against them.
- Fail-closed controls when identity, tenant, API, orchestrator, or workflow state prerequisites are absent.

Native iOS/Android applications are outside this delivery. M2 billing and ledger behavior are not part of the console.

## Local verification evidence

Verified against production builds on 2026-10-27 and 2026-10-28:

- TypeScript production build and ESLint passed.
- npm full audit reported zero known vulnerabilities.
- Manifest, install icons, and nonempty generated service worker were served successfully.
- Twenty-six browser checks passed across navigation, primary CTAs, queue selection, tier pricing, disabled preview mutations, modal close/Escape/backdrop behavior, dialog focus, install guidance, and safety controls.
- Layouts at 375, 768, 1024, and 1440 pixels had no horizontal overflow; every principal page was exercised at 375 pixels.
- The service worker controlled a reload, offline reload restored the mobile shell, and cache inspection found neither `runtime-config.json` nor `/v1/` API entries.
- No unexpected browser console errors, asset HTTP errors, or failed requests occurred. The deliberate offline reload's network-disconnected message was excluded from that assertion.
- Two additional authenticated-isolation checks used synthetic browser-only OIDC/API responses: preview campaign and Studio records and their approval controls were absent, and live empty states rendered instead.
- Twenty-two targeted checks on 2026-10-28 covered authenticated live feeds, campaign approval identity, refreshed workflow state, real QA gallery rendering, exact manifest-hash submission, image-load approval gating, post-review refresh, failed/expired signed-URL recovery, mobile overflow, and disabled preview actions. A separate current-build check confirmed service-worker control, offline mobile reload, and no runtime configuration or API responses in Cache Storage.

These checks verify the frontend and service implementation, not a live tenant, Temporal cluster, object store, provider delivery, deployed OIDC service, or staging acceptance. Synthetic responses used for browser testing are not operational evidence.

## Deployment checklist

1. Run `npm install`, `npm run build`, `npm run lint`, and `npm audit --audit-level=low` in `apps/console` using Node.js 22.12+.
2. Serve `dist/` over HTTPS with a restrictive content security policy reviewed for the deployment's OIDC and API origins.
3. Supply `dist/runtime-config.json` through deployment configuration. Never build secrets or tenant records into it.
4. Register the exact console redirect URI on a public PKCE client; configure MFA, token audience, tenant claims, roles, and API CORS in their respective services.
5. Confirm unauthenticated preview mutations remain disabled and authenticated sessions contain no preview queue records.
6. Exercise real tenant-scoped Core calls, including role-denied and cross-tenant attempts, against staging.
7. Deploy the orchestrator and exercise real Temporal list/query/start/signal behavior, including role-denied and cross-tenant attempts. Do not infer delivery from an accepted submission; require the refreshed workflow state.
8. Verify actual tenant-prefixed QA objects, short-lived signed URLs, stale-manifest denial, image-load gating, and post-approval delivery behavior in staging. Human approval remains mandatory.
9. Complete the production image-delivery and sixteen-check staging acceptance workflows before calling M1 accepted.

The console's connection indicators report configuration/session state only. They are not proof of provider health, four-channel delivery, GPU output, recovery, Wazuh detection, SLO compliance, turnaround, or cost reconciliation.
