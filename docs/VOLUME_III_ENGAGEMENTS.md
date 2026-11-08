# Volume III plan-only delivery engagements

Status date: 2026-11-08. This increment adds the first implementation slice for Offers **27–34**: authenticated, tenant-scoped engagement planning and mandatory human approval. It does not add production service delivery, commercial terms, subscriptions, invoices, charges, ledger postings, or external provider integrations.

## Implemented boundary

- `POST /v1/engagements` starts an `EngagementWorkflow` on the `engagements-v1` Temporal task queue. Only a delivery operator can start one.
- `GET /v1/engagements` lists only the authenticated tenant's workflows; `GET /v1/engagements/{engagement_id}` returns the current plan and review state.
- `POST /v1/engagements/{engagement_id}/review` allows a delivery operator or analyst to approve or reject the current plan. The request must contain its exact SHA-256 digest.
- Tenant identity and reviewer identity come from OIDC claims. Client-supplied tenant fields and unknown commercial fields are rejected.
- Each offer has server-defined mandatory controls. Clients cannot replace them through the API.
- The planning activity writes deterministic JSON and a manifest under `{tenant}/engagements/{offer}/{engagement}/`. It verifies the content digest before storage.
- Temporal holds durable workflow state. This slice adds no parallel business-status database or queue.

## State and approval contract

The workflow moves through `validating` → `planning` → `awaiting-approval`, then terminates as `approved` or `rejected` after one digest-bound decision. A review waiting longer than 30 days becomes `approval-expired`. Reviews in the wrong state, reviews with stale digests, missing reasons, and second terminal decisions are rejected.

`approved` means only that the current plan was approved. It does **not** mean customer delivery, production deployment, external integration activation, fabrication, publication, monitoring activation, or commercial activation has occurred. The status payload explicitly reports `delivery_mode: plan-only`, `charges_enabled: false`, and `external_actions_enabled: false`.

## Explicitly unavailable

- No Offer 27–34 tiers, prices, entitlements, subscriptions, invoices, charge reservations, settlements, or TigerBeetle postings.
- No calls to the existing chargeable Offer workflow activities.
- No website publication, ad rendering/serving, tutor deployment, agent installation/tool invocation, CAD generation/fabrication, audio publication, bid retrieval/submission, monitoring activation, or customer communication.
- No assertion that the eight inspiration repositories have been deployed, license-cleared, security-reviewed, or accepted in staging.

A later implementation request must separately define approved commercial terms, least-privilege provider connectors, data controls, acceptance tests, and another human gate before any external action.

## Operator PWA

The console's **Engagements** workspace exposes the complete plan-review loop for Offers 27–34: authenticated queue and detail reads, engagement creation, mandatory-control and exact-JSON inspection, and approval or rejection of the currently displayed plan digest. The interface handles pending, empty, disconnected, failed, stale-plan, approved, rejected, and expired states without substituting mock engagements or enabling commercial and external-delivery actions.

Run `npm run build` and `npm run lint` under `apps/console`. Component interaction verification covers creation payloads, both review decisions, exact digest binding, stale-plan reload, disconnected operation, pending plans, terminal reviews, and expiry. Production PWA generation and responsive browser interaction are verified separately from live Temporal/S3 acceptance.

## Source verification

Run the orchestrator and offer-worker Python test suites. The engagement tests cover deterministic plans, tenant object boundaries, digest validation, all eight offer-control sets, invalid jobs, invalid states, stale digests, reviewer identity, tenant isolation, RBAC, and rejection of injected financial fields. These are source-level tests, not a live Temporal/S3 staging acceptance result.
