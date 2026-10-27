# M1 Console Delivery and Verification

Status: responsive M1 PWA source and browser-verified preview are implemented. Production OIDC, deployed orchestrator HTTP contracts, live queue feeds, Studio object review, production images, and staging acceptance remain deployment gates.

## Delivered scope

- Responsive Overview, Onboarding, Campaigns, Studio QA, Offers, and Settings & safety screens.
- Desktop navigation rail, mobile navigation, safe-area spacing, visible focus states, reduced-motion handling, and keyboard-contained dialogs with trigger-focus restoration.
- Installable standalone PWA with local fonts/icons, static-shell offline reload, update prompt, and browser-install fallback.
- OIDC Authorization Code with PKCE and tab-scoped session storage; no client secret.
- Real Core API contracts for catalog, tenant, workspace, subscription, outbound suspension, and recipient suppression.
- Configurable campaign/Studio orchestrator submission and human-review calls.
- Explicit preview/live separation. Authenticated sessions discard all illustrative campaign and Studio records rather than permitting actions against them.
- Fail-closed controls when identity, tenant, API, orchestrator, or workflow state prerequisites are absent.

Native iOS/Android applications are outside this delivery. M2 billing and ledger behavior are not part of the console.

## Local verification evidence

Verified against the production build on 2026-10-27:

- TypeScript production build and ESLint passed.
- npm full audit reported zero known vulnerabilities.
- Manifest, install icons, and nonempty generated service worker were served successfully.
- Twenty-six browser checks passed across navigation, primary CTAs, queue selection, tier pricing, disabled preview mutations, modal close/Escape/backdrop behavior, dialog focus, install guidance, and safety controls.
- Layouts at 375, 768, 1024, and 1440 pixels had no horizontal overflow; every principal page was exercised at 375 pixels.
- The service worker controlled a reload, offline reload restored the mobile shell, and cache inspection found neither `runtime-config.json` nor `/v1/` API entries.
- No unexpected browser console errors, asset HTTP errors, or failed requests occurred. The deliberate offline reload's network-disconnected message was excluded from that assertion.
- Two additional authenticated-isolation checks used synthetic browser-only OIDC/API responses: preview campaign and Studio records and their approval controls were absent, and live empty states rendered instead.

These checks verify the frontend implementation, not a live tenant, provider delivery, deployed OIDC service, or staging acceptance. Synthetic responses used for isolation testing are not operational evidence.

## Deployment checklist

1. Run `npm install`, `npm run build`, `npm run lint`, and `npm audit --audit-level=low` in `apps/console` using Node.js 22.12+.
2. Serve `dist/` over HTTPS with a restrictive content security policy reviewed for the deployment's OIDC and API origins.
3. Supply `dist/runtime-config.json` through deployment configuration. Never build secrets or tenant records into it.
4. Register the exact console redirect URI on a public PKCE client; configure MFA, token audience, tenant claims, roles, and API CORS in their respective services.
5. Confirm unauthenticated preview mutations remain disabled and authenticated sessions contain no preview queue records.
6. Exercise real tenant-scoped Core calls, including role-denied and cross-tenant attempts, against staging.
7. Leave orchestrator actions disabled until its HTTP boundary and live queue/read behavior are implemented and verified. Do not infer delivery from a successful form submission.
8. Connect actual QA assets through the object-delivery interface before permitting Studio approval. Human approval remains mandatory.
9. Complete the production image-delivery and sixteen-check staging acceptance workflows before calling M1 accepted.

The console's connection indicators report configuration/session state only. They are not proof of provider health, four-channel delivery, GPU output, recovery, Wazuh detection, SLO compliance, turnaround, or cost reconciliation.
