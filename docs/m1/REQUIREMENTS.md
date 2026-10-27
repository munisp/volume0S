# M1 requirement matrix

Scope comes from the architecture roadmap: **Content Cadence, Shopfront Studio, and onboarding**, with ListMonk and Postiz embedded per tenant. Offer 01 is included in the commercial catalog and email adapter because it is the ListMonk service line. Other service lines remain outside this slice.

Status terms match `docs/REQUIREMENTS.md`: **tested**, **configured**, **blocked**, or **later**. No status here substitutes for staging acceptance.

| Requirement | M1 implementation | Status |
|---|---|---|
| Q1 offer/pricing model | Versioned NGN minor-unit catalog for offers 01/02/06 and the launch combo; prices remain operator-review-required | tested |
| MSG-3 ListMonk | Pinned HelmForge chart, external PostgreSQL, existing-secret credentials, no ingress; API adapter requires tenant-list binding | configured / adapter tested |
| MSG-4 Postiz | Pinned upstream image, external PostgreSQL/Redis, existing-secret credentials, no ingress; approval required before API scheduling; tenant-specific Dapr credentials and enabled-integration validation for onboarding and scheduling | configured / adapter tested |
| MSG-5 usage events | Dispatch result creates a tenant/channel/campaign/cost usage record at the event boundary; Kafka publisher integration is configured separately | tested / configured |
| MSG-6 suppression | PostgreSQL suppression registry with HMAC-SHA-256 recipient digests, normalized recipients, lifecycle/channel checks before every new provider call; fail closed | tested / staging acceptance required |
| MSG-7 kill switch | Persistent tenant suspension check before every new provider call; fail closed | tested / staging acceptance required |
| FLW-1 onboarding | Durable Keycloak → tenancy → ListMonk → existing Postiz OAuth validation → welcome saga with reverse compensation for owned resources; Postiz validation is read-only and TigerBeetle remains M2 | tested / configured |
| FLW-2 cadence | Existing approval signal is bound to provider dispatch; staging four-channel E2E remains | configured |
| AI-4 studio | Versioned ComfyUI graph, upload/submit/poll activities, tenant-prefixed asset keys, QA approve/reject signal, delivery only after approval | tested / configured |
| OBS-2/6 SLO dashboards and probes | Reviewed Core API, per-cycle dispatch, approval/Studio dashboard and alert definitions; fail-closed absent-series alerts; metric instrumentation, five-minute probes, WhatsApp receiver, and live traces still require implementation/cluster validation | configured / staging acceptance required |
| NFR-2/3/4, BR-5/23/24 acceptance | Fail-closed preflight, 16-check evidence catalog, checksummed artifacts, and runbook cover SLO, recovery, onboarding, kill-switch, security, GPU, and cost drills | tested tooling / staging evidence blocked |
| OBS-3 Wazuh | Official v4.14.9 source reviewed; upstream bootstrap credentials/certificates must be replaced by the secret pipeline before activation | blocked |
| M1 outcome | First paying clients onboarded end to end in staging | blocked on real cluster, providers, OIDC/MFA, and acceptance evidence |

## Messaging and provisioning persistence boundary

`svc-tenancy` now owns tenant-scoped dispatch decisions and idempotency results in PostgreSQL. Internal workers authenticate with `VOLUMEOS_SERVICE_TOKEN`; suppression digests require a separate `SUPPRESSION_HMAC_SECRET` of at least 32 bytes. Neither secret nor recipient addresses are written to logs or provider responses.

`svc-provisioning` creates idempotent, service-account-only Keycloak tenant clients with a hardcoded `tenant_id` claim. For Postiz it retrieves `postiz-{tenant_id}-api-key` through the configured Dapr secret store and validates an existing enabled integration through the official API. Customers must complete social OAuth themselves. VolumeOS does not invent a connected channel and never deletes that customer-owned integration during saga compensation.

The repository supplies environment names and secret-injection manifests, not live credentials. A private overlay must supply the `volumeos-tenant-secrets` Dapr component and tenant API keys before staging onboarding can succeed.

## Commercial-data boundary

The Q1 launch kit says its founding rates are valid for 90 days. The catalog therefore records the supplied price book without asserting that those prices are currently active. An operator must review and activate a dated price book before using it for quotes or billing. No billing postings are created in M1; TigerBeetle remains M2.

## Staging acceptance and observability assets

`docs/m1/STAGING_ACCEPTANCE.md` and `scripts/staging_acceptance.py` provide a read-only preflight and a fail-closed evidence workflow. The 16-check catalog maps operational drills to requirement IDs; a passing check needs a real artifact, and final verification requires passing preflight plus all checks with matching SHA-256 artifact hashes. The utility tests prove the tooling behavior, not staging health.

`monitoring/m1/` contains the M1 PrometheusRule, ServiceMonitor, and Grafana dashboard source. `docs/m1/OBSERVABILITY.md` defines the required metrics and activation boundary. These resources are deliberately not auto-synced before service/probe instrumentation exists; no-data or absent-series conditions are not successful SLO evidence. Pay probes and TigerBeetle recovery remain M2 scope.

## Acceptance still required

1. Render and sync pinned components in staging; verify their chart/image values against upstream.
2. Create per-tenant ListMonk lists, install each tenant's Postiz API key in the Dapr secret store, complete customer social OAuth, and validate the selected enabled integration; never share API credentials across tenants.
3. Prove suppressed contacts and suspended tenants produce zero provider calls against persistent PostgreSQL policy state.
4. Run onboarding with injected failures and verify every compensation action and audit event; confirm Postiz validation remains read-only.
5. Run Content Cadence across email, SMS, WhatsApp, and social; only approved content may publish.
6. Run a real GPU-backed ComfyUI pack, reject one asset with a reason, and verify rejected assets never reach delivery prefixes.
7. Produce 48-hour turnaround, usage/provider-cost reconciliation, and SLO dashboard evidence.
