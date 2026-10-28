# Offer operations source guide

Status date: 2026-10-28. This document covers Offers 03, 04, 05, 07, and 08. **Source implemented and locally tested does not mean provider, staging, image-publication, or production acceptance has passed.**

## Commercial boundary

The tenancy catalog includes all five offers with `CommercialStatus = source-only-unpriced` and no tiers. No price was inferred from the source documents. The console displays these entries as unpriced and disables subscription creation. Commercial terms must be reviewed and explicitly configured before charging them.

## Offers 03, 05, 07, and 08 — prepaid batch jobs

The orchestrator exposes tenant-scoped Temporal operations:

- `GET /v1/offers/jobs`
- `POST /v1/offers/jobs`
- `GET /v1/offers/jobs/{job_id}`
- `POST /v1/offers/jobs/{job_id}/review`

Jobs execute as `OfferWorkflow` on `offers-v1` with workflow ID `offer:{tenant_id}:{job_id}`. The authenticated token supplies the tenant; the browser form never supplies it. Start requires positive NGN minor-unit prepaid, estimated-cost, and job-ceiling amounts.

The worker reserves before execution, validates offer-specific source data and model/template versions, calls configured providers, applies the AI budget record, and stores content addressed by exact SHA-256. A review signal is accepted only for the current artifact while the job is `awaiting-review`. Offer 08 requires approvals from two distinct analysts. Rejection or pre-release failure voids the reservation; successful exact-artifact release precedes posting. A failure after release completion must not void an already released job as though no artifact existed.

Provider configuration is supplied through the worker deployment: Temporal, PostgreSQL, billing, S3, WhisperX, Docling, browser registry agent, privacy AI gateway, finance renderer, budget ceilings, and secret references. HTTPS and registry allowlists remain mandatory where applicable.

## Offer 04 — AI Front Desk

`apps/core/svc-conversations` exposes signed WhatsApp intake and authenticated operator controls:

- `POST /v1/webhooks/whatsapp/{tenant_id}`
- `GET /v1/conversations`
- `GET /v1/conversations/{conversation_id}`
- `GET /v1/conversations/{conversation_id}/drafts/{draft_sha256}`
- `POST /v1/conversations/{conversation_id}/drafts/{draft_sha256}/review`
- `POST /v1/conversations/{conversation_id}/drafts/{draft_sha256}/send`
- `POST /v1/conversations/{conversation_id}/bookings`
- `POST /v1/conversations/{conversation_id}/suppression`

Safety properties:

1. The raw webhook body is bounded to 64 KiB and verified using `X-Hub-Signature-256` before parsing.
2. `phone_number_id` must belong to the path tenant. Message replays do not repeat AI calls; conflicting replays fail.
3. The consent subject is `whatsapp:+{recipient}`, stored as an HMAC-SHA-256 digest. Current `service-delivery` and `ai-processing` consent plus suppression state are checked.
4. External AI access goes only through `svc-privacy`. A draft requires numeric confidence and an explicit model version.
5. Review binds the exact draft digest. Sending is a separate role-checked action and checks consent/suppression again.
6. WhatsApp send and booking hold/confirm are idempotent. Production state uses PostgreSQL with forced RLS; the in-memory store is test-only.

Required deployment secrets/configuration include PostgreSQL, OIDC/JWKS, the subject-digest key, WhatsApp app secret and access token, the privacy service, booking service, service token, and a pinned front-desk model version. Do not place these values in runtime-config JSON or source control.

## Console controls

`OfferOperationsSection.tsx` provides tenant queue refresh, job start, exact-artifact approval/rejection, exact-draft review, separate approved send, booking, and suppression. Empty or unavailable live APIs produce empty queues or explicit errors; no illustrative conversation or job is substituted for live state. The console runtime key is `conversationsApiUrl` and remains empty in source.

## Source verification

The current local gate covers:

- `apps/core/svc-conversations`: 7 tests passed.
- `apps/workers/offer-worker`: 11 tests passed.
- `apps/core/svc-orchestrator`: 39 tests passed.
- Console: TypeScript production build, PWA bundle, ESLint, and production-dependency audit passed; desktop and 390px mobile views were opened and checked. The full development audit currently reports four low-severity upstream PWA build-chain advisories; the proposed forced downgrade is breaking and was not applied.
- `scripts/verify_repo.py`: includes deployment, empty runtime configuration, exact-review, consent, RLS, provider, and settlement source invariants.

Run the complete local entry point with `make test-offer-operations` after installing each package's test dependencies.

## Acceptance still blocked on real infrastructure

No local test proves a Temporal cluster executed a job, a registry or model provider returned production evidence, WhatsApp delivered a message, a booking provider confirmed a slot, an object store released bytes, or a ledger cluster posted funds. Before operational acceptance, publish immutable nonroot images, run migrations against a non-owner RLS role, deploy all three overlays as appropriate, configure OIDC/CORS and provider sandboxes, and capture tenant-isolation, replay, timeout, rejection, suppression, compensation, restore, and delivery evidence. Native device and other milestone acceptance remain separate gates.
