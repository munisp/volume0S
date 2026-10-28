# VolumeOS

Open-source-first operating platform for the Naira Volume Playbook: multi-tenant service delivery, prepaid operations, durable workflows, and geospatial market intelligence for Nigerian MSMEs.

This repository contains the **M0/M1 vertical slices plus source implementations for M2 money, M3 data and geospatial intelligence, M4 privacy/security, native field applications, CRM/leads, and Offers 01–08**. Passing source gates do not mean that staging, production images, external providers, a live cluster, or physical-device acceptance has passed.

## Implemented

- **Tenancy (Go):** tenant → workspace → subscription model for offers 01–08; validated lifecycle transitions; optimistic concurrency; outbound-messaging kill switch with an audit reason.
- **Isolation and identity:** RS256 JWT validation against Keycloak JWKS, issuer/audience/expiry enforcement, `tenant_id` on every scoped request, role checks, PostgreSQL row-level security, and cross-tenant tests. Header authentication is a development-only opt-in and is rejected in production.
- **Persistence:** PostgreSQL migrations executed under an advisory lock, per-tenant transactions, no derived financial balances in PostgreSQL.
- **Campaign orchestration (Python/Temporal):** draft → explicit human approval signal → reserve → dispatch → post, with a 24-hour approval timeout and void-on-failure compensation. Activities use real HTTP endpoints, idempotency keys, tenant scope, retries, and heartbeats; there are no simulated provider successes.
- **Commercial catalog (Go):** versioned NGN minor-unit prices and entitlements for Come-Back Engine, Content Cadence, Shopfront Studio, and the launch bundle. Supplied Q1 founding rates remain operator-review-required. Offers 03/04/05/07/08 are source-only and explicitly unpriced, so the console cannot create subscriptions or charges for them.
- **Guarded messaging adapters:** ListMonk and Postiz clients with tenant bindings, approval validation, persistent PostgreSQL suppression/kill-switch policy and idempotency results, tenant-scoped Dapr Postiz credentials, and usage-event emission. Suppression records contain HMAC recipient digests rather than recipient addresses.
- **Internal provisioning:** scoped service authentication, idempotent Keycloak tenant clients with hardcoded tenant claims, and read-only validation of existing enabled Postiz OAuth integrations. Customer social connections are never fabricated or deleted by compensation.
- **Onboarding saga:** Keycloak → tenancy → ListMonk/Postiz validation → activation → welcome, with reverse compensation for resources VolumeOS owns. TigerBeetle account creation remains M2.
- **Shopfront Studio:** versioned ComfyUI image graph, GPU job submission/polling, tenant-prefixed object storage, human QA approval/rejection, and WhatsApp/Jumia delivery formats. Rejected or timed-out packs are never delivered.
- **Money and prepaid operations (Rust/Python):** deterministic two-phase reserve/post/void semantics, official TigerBeetle client transport, signed collection adapters, billing/dunning, closed usage rollups, documents, and financial reports. TigerBeetle remains the sole balance store; no live cluster or provider acceptance is implied.
- **Event contracts:** Protobuf envelopes and usage, money, lead, message-status, and audit-publish records; `money.events` is explicitly a mirror, not a ledger.
- **GitOps:** pinned Argo CD applications, Dapr mTLS, APISIX + open-appsec, Strimzi Kafka and seven topics, PostgreSQL, Redis, MinIO buckets, Keycloak, OpenSearch, Temporal, observability, Kubecost, and OpenCTI. M1 adds reviewed ListMonk and Postiz packaging; Wazuh is pinned but deliberately not auto-synced before its private secret overlay.
- **Operator console and orchestrator (React/PWA + Python):** responsive desktop/mobile onboarding, catalog, approval, Studio QA, offer operations, and safety screens; OIDC PKCE; installable offline application shell; real Core API integration; tenant-scoped Temporal workflow feeds and mutations; actual object-backed QA image review; exact-manifest/artifact approval binding; and fail-closed preview/live separation.
- **Offer 04 AI Front Desk:** signed raw-body WhatsApp intake, tenant/channel binding, replay protection, HMAC subject digests, current consent and suppression checks, redacted-AI-only drafting, numeric confidence, exact-draft human approval, separate send, idempotent booking, and PostgreSQL/RLS persistence.
- **Offers 03/05/07/08:** prepaid Temporal jobs on `offers-v1`, provider/source validation, budget ceilings, object-digested artifacts, human review, two distinct analysts for funding packs, release-before-post settlement, and void compensation before release.
- **CRM, data, privacy, and field source:** tenant-scoped lead APIs; bronze/silver/gold and geospatial contracts; privacy/DSAR/redaction and security-operations assets; native Android/iOS PKCE, encrypted outbox, and resumable census synchronization. These are source-verified boundaries, not claims of live analytics, provider, cluster, or device success.
- **Verification:** Go, Python, Rust, and TypeScript checks; static manifest/source checks; CI for tests, lint, and Kustomize rendering.
- **M1 observability and acceptance tooling:** Core request, campaign cycle/provider, Studio outcome, and five-minute login/send synthetic instrumentation; internal ServiceMonitors; fail-closed, read-only staging preflight; requirement-mapped evidence capture with SHA-256 artifact verification; and reviewed SLO dashboard/alert definitions. These tools do not assert that staging has passed. The dedicated provider-sandbox send endpoint, private probe configuration, published images, and live evidence remain required.

## Intentionally not represented as complete

ListMonk and Postiz are configured but still require staging health, tenant provisioning, provider, backup, and isolation acceptance. The private `volumeos-tenant-secrets` Dapr component and per-tenant Postiz keys remain operator-supplied. Wazuh is not active before its bootstrap credentials and certificate inputs are replaced through a reviewed private overlay. The ComfyUI GPU runtime and model licenses are operator-supplied acceptance items. Live TigerBeetle, payment providers, Temporal, object storage, Flink/Spark/Sedona/Ray/DataFusion workloads, WhatsApp/AI/booking providers, registry credentials, and external operator deployment still require real infrastructure and evidence. The orchestrator, offer services, and console require deployed OIDC/CORS, production image publication, and operational acceptance; native projects still require signed builds and physical-device tests.

## Repository layout

```text
apps/console/                   Responsive installable M1 operator console
apps/core/svc-tenancy/          Go API, OIDC, PostgreSQL, messaging policy, tests
apps/core/svc-provisioning/     Keycloak and Postiz onboarding boundary, tests
apps/core/svc-orchestrator/     OIDC API, Temporal reads/signals, Studio and offer reviews
apps/core/svc-conversations/    Signed WhatsApp intake, approved front-desk replies, bookings
apps/core/svc-leads/            Tenant-scoped CRM and lead-engine APIs
apps/core/svc-privacy/          Consent, DSAR, redaction, and privacy evidence
apps/workers/campaign-worker/   Campaign/onboarding workflows, messaging adapters, tests
apps/workers/studio-worker/     ComfyUI workflow, QA gate, asset delivery, tests
apps/workers/offer-worker/      Prepaid batch Offers 03/05/07/08 on offers-v1
apps/native/field-app/          Android/iOS offline census and resumable synchronization
apps/workers/synthetic-probe/   Five-minute login/send journeys and probe tests
bootstrap/m1/postiz/           Reviewed upstream-image packaging with existing secrets
libs-rust/tb-adapter/           Two-phase reservation contract and tests
proto/volumeos/v1/              Backbone event contracts
bootstrap/argocd/               AppProject, root app, pinned child applications
bootstrap/platform/             Kafka, topics, identity/worker config, API route
deploy/overlays/                dev / stage / prod Kustomize overlays
policies/                       Default-deny network policy baseline
scripts/verify_repo.py          Cluster-independent repository gate
scripts/staging_acceptance.py   Fail-closed staging preflight and evidence verifier
scripts/m1_image_delivery.py    Clean-revision build, nonroot smoke, GHCR digest handoff
monitoring/m1/                  M1 metric-dependent alerts, scrape config, dashboard
docs/                           Requirements, ADRs, observability, image and acceptance runbooks
```

## Local verification

Required tools: Go 1.23+, Python 3.12+, Rust 1.82+, Node.js 22.12+, npm, and PyYAML.

```bash
python3 -m pip install pyyaml
python3 scripts/verify_repo.py
python3 -m unittest discover -s scripts/tests -v

cd apps/console
npm install
npm run build
npm run lint
npm audit --omit=dev --audit-level=low

cd ../core/svc-tenancy
go test ./...

cd ../svc-provisioning
go test ./...

cd ../svc-orchestrator
python3 -m pip install -e '.[test]'
PYTHONPATH=. python3 -m pytest

cd ../../workers/campaign-worker
python3 -m pip install -e '.[test]'
PYTHONPATH=. python3 -m pytest

cd ../studio-worker
python3 -m pip install -e '.[test]'
PYTHONPATH=. python3 -m pytest

cd ../synthetic-probe
python3 -m pip install -e '.[test]'
PYTHONPATH=. python3 -m pytest

cd ../offer-worker
python3 -m pip install -e '.[test]'
PYTHONPATH=. python3 -m pytest

cd ../../core/svc-conversations
python3 -m pip install -e '.[test]'
PYTHONPATH=. python3 -m pytest

cd ../svc-orchestrator
python3 -m pip install -e '.[test]'
PYTHONPATH=. python3 -m pytest

cd ../svc-privacy
python3 -m pip install -e '.[test]'
PYTHONPATH=. python3 -m pytest

cd ../svc-leads
python3 -m pip install -e '.[test]'
PYTHONPATH=. python3 -m pytest

cd ../../..
python3 scripts/verify_m3.py
python3 scripts/verify_m4.py
python3 scripts/verify_native.py
python3 -m pytest scripts/test_native_contracts.py scripts/test_restore_drill.py

cd libs-rust/tb-adapter
cargo test
```

Run the API locally with the explicitly non-production stores/auth:

```bash
cd apps/core/svc-tenancy
STORE=memory VO_ENV=dev ALLOW_INSECURE_HEADER_AUTH=true go run .

curl -H 'X-VolumeOS-Tenant: demo' -H 'X-VolumeOS-Roles: super-operator' \
  -H 'Content-Type: application/json' \
  -d '{"name":"Demo Gym","slug":"demo-gym"}' \
  http://localhost:8080/v1/tenants/current
```

## Cluster bring-up

Prerequisites: an in-region Kubernetes cluster, a default expandable StorageClass, `kubectl`, Helm 3.14+, DNS/TLS ownership, and secrets managed through SOPS/Dapr for shared environments.

```bash
make apply-namespaces
make bootstrap-argocd

# Supply values through your secret-injection path; do not commit them.
export DATABASE_URL='postgres://…'
export MINIO_ROOT_USER='…'
export MINIO_ROOT_PASSWORD='…'
export GRAFANA_ADMIN_PASSWORD='…'
make configure-secrets
# For M1, also supply ListMonk/Postiz/S3 variables plus strong random values for:
# VOLUMEOS_SERVICE_TOKEN, SUPPRESSION_HMAC_SECRET, KEYCLOAK_ADMIN_CLIENT_ID,
# KEYCLOAK_ADMIN_CLIENT_SECRET, and optionally DAPR_API_TOKEN.
# Install each postiz-{tenant_id}-api-key in the private volumeos-tenant-secrets Dapr store.
# make configure-m1-secrets
make apply-apps

kubectl -n argocd get applications
kubectl apply -k deploy/overlays/dev
```

Sync waves order resources during an Argo sync operation; they are **not** a substitute for health checks or operational acceptance. Before stage/prod, verify component values against the pinned upstream charts, configure the Keycloak realm/clients/MFA, install the APISIX OIDC plugin configuration through SOPS, enforce WAF policy after staging review, and run backup/restore and kill-switch drills.

### M0 acceptance still requiring a real cluster

- All applications and pods healthy; Kafka RF=3 and all seven topics present.
- Keycloak MFA and OIDC accepted at the edge and service; invalid/expired/wrong-tenant tokens fail closed.
- PostgreSQL migrations and RLS verified against a non-owner application role.
- Temporal worker polls `campaigns-v1`; approval timeout and dispatch-failure void tested end to end.
- No direct public service endpoints; APISIX is the only ingress.
- Restore drill evidence for PostgreSQL and MinIO; quarterly drill scheduled.

### M1 acceptance still requiring real providers and a cluster

- Accept the real Keycloak and Postiz provisioner against staging providers; no fake success responses are included.
- Verify per-tenant ListMonk lists and Dapr Postiz credentials, customer OAuth integration validation, double opt-in and persistent suppression, provider webhooks, and zero sends while suspended.
- Run the onboarding failure-injection drill and confirm every compensating action and optimistic tenancy transition.
- Provision a reviewed GPU runtime/model, run a real Studio pack, and verify rejection isolation and 48-hour turnaround.
- Replace Wazuh bootstrap credentials/certificates before activation, then run the purple-team detection drill.
- Build and publish the changed service/worker/probe images, configure the dedicated synthetic sandbox send endpoint and credentials, verify five-minute login/send evidence and alert delivery, reconcile provider cost with `usage.events`, and publish M1 SLO dashboards.
- Deploy the console with real PKCE/MFA and tenant-scoped API/CORS policy; verify live Core actions, no preview records after authentication, deployed orchestrator contracts, and actual Studio asset review before enabling operational approvals.

Use [`docs/m1/IMAGE_DELIVERY.md`](docs/m1/IMAGE_DELIVERY.md) for production-fidelity builds, nonroot smoke checks, GHCR publication, and immutable digest verification. Then use [`docs/m1/STAGING_ACCEPTANCE.md`](docs/m1/STAGING_ACCEPTANCE.md) to run the preflight and capture real evidence. The current repository supplies instrumentation, the acceptance process, and monitoring definitions; it does not contain a passing staging evidence bundle. See [`docs/m1/OBSERVABILITY.md`](docs/m1/OBSERVABILITY.md) before applying `monitoring/m1/`. Console setup, browser verification, and deployment gates are documented in [`docs/m1/CONSOLE.md`](docs/m1/CONSOLE.md).

See [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md), [`docs/m1/REQUIREMENTS.md`](docs/m1/REQUIREMENTS.md), [`docs/ROADMAP_REQUIREMENTS.md`](docs/ROADMAP_REQUIREMENTS.md), and [`docs/OFFER_OPERATIONS.md`](docs/OFFER_OPERATIONS.md) for requirement-level status and acceptance boundaries. The checks actually executed in this delivery run, and the toolchains/providers not available, are recorded in [`docs/VERIFICATION.md`](docs/VERIFICATION.md).
