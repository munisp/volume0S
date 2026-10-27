# VolumeOS

Open-source-first operating platform for the Naira Volume Playbook: multi-tenant service delivery, prepaid operations, durable workflows, and geospatial market intelligence for Nigerian MSMEs.

This repository currently implements the **M0 foundation plus an M1 offer, messaging, onboarding, and Studio vertical slice**. It does not claim that M1 staging acceptance or the later money, lakehouse, AI-runtime, security-operations, or operator-console milestones are complete.

## Implemented

- **Tenancy (Go):** tenant → workspace → subscription model for offers 01–08; validated lifecycle transitions; optimistic concurrency; outbound-messaging kill switch with an audit reason.
- **Isolation and identity:** RS256 JWT validation against Keycloak JWKS, issuer/audience/expiry enforcement, `tenant_id` on every scoped request, role checks, PostgreSQL row-level security, and cross-tenant tests. Header authentication is a development-only opt-in and is rejected in production.
- **Persistence:** PostgreSQL migrations executed under an advisory lock, per-tenant transactions, no derived financial balances in PostgreSQL.
- **Campaign orchestration (Python/Temporal):** draft → explicit human approval signal → reserve → dispatch → post, with a 24-hour approval timeout and void-on-failure compensation. Activities use real HTTP endpoints, idempotency keys, tenant scope, retries, and heartbeats; there are no simulated provider successes.
- **M1 offer catalog (Go):** versioned NGN minor-unit prices and entitlements for Come-Back Engine, Content Cadence, Shopfront Studio, and the launch bundle. Supplied Q1 founding rates remain operator-review-required rather than being represented as currently active prices.
- **Guarded messaging adapters:** ListMonk and Postiz clients with tenant bindings, approval validation, persistent PostgreSQL suppression/kill-switch policy and idempotency results, tenant-scoped Dapr Postiz credentials, and usage-event emission. Suppression records contain HMAC recipient digests rather than recipient addresses.
- **Internal provisioning:** scoped service authentication, idempotent Keycloak tenant clients with hardcoded tenant claims, and read-only validation of existing enabled Postiz OAuth integrations. Customer social connections are never fabricated or deleted by compensation.
- **Onboarding saga:** Keycloak → tenancy → ListMonk/Postiz validation → activation → welcome, with reverse compensation for resources VolumeOS owns. TigerBeetle account creation remains M2.
- **Shopfront Studio:** versioned ComfyUI image graph, GPU job submission/polling, tenant-prefixed object storage, human QA approval/rejection, and WhatsApp/Jumia delivery formats. Rejected or timed-out packs are never delivered.
- **Money contract (Rust):** deterministic two-phase reserve/post/void state machine with replay and idempotency-conflict tests. The live TigerBeetle transport is an M2 deliverable.
- **Event contracts:** Protobuf envelopes and usage, money, lead, message-status, and audit-publish records; `money.events` is explicitly a mirror, not a ledger.
- **GitOps:** pinned Argo CD applications, Dapr mTLS, APISIX + open-appsec, Strimzi Kafka and seven topics, PostgreSQL, Redis, MinIO buckets, Keycloak, OpenSearch, Temporal, observability, Kubecost, and OpenCTI. M1 adds reviewed ListMonk and Postiz packaging; Wazuh is pinned but deliberately not auto-synced before its private secret overlay.
- **Verification:** Go, Python, and Rust tests; static manifest/source checks; CI for tests, lint, and Kustomize rendering.

## Intentionally not represented as complete

ListMonk and Postiz are configured but still require staging health, tenant provisioning, provider, backup, and isolation acceptance. The private `volumeos-tenant-secrets` Dapr component and per-tenant Postiz keys remain operator-supplied. Wazuh is not active: the upstream base contains bootstrap credentials and certificate inputs that must be replaced through a reviewed private overlay. The ComfyUI GPU runtime and model licenses are operator-supplied acceptance items. The APISIX-to-Keycloak OIDC plugin secret, TigerBeetle transport, Flink/Spark/Sedona/Ray workloads, DataFusion service, and console remain milestone work. The Rust reservation book is a contract model, not a running TigerBeetle adapter.

## Repository layout

```text
apps/core/svc-tenancy/          Go API, OIDC, PostgreSQL, messaging policy, tests
apps/core/svc-provisioning/     Keycloak and Postiz onboarding boundary, tests
apps/workers/campaign-worker/   Campaign/onboarding workflows, messaging adapters, tests
apps/workers/studio-worker/     ComfyUI workflow, QA gate, asset delivery, tests
bootstrap/m1/postiz/           Reviewed upstream-image packaging with existing secrets
libs-rust/tb-adapter/           Two-phase reservation contract and tests
proto/volumeos/v1/              Backbone event contracts
bootstrap/argocd/               AppProject, root app, pinned child applications
bootstrap/platform/             Kafka, topics, identity/worker config, API route
deploy/overlays/                dev / stage / prod Kustomize overlays
policies/                       Default-deny network policy baseline
scripts/verify_repo.py          Cluster-independent repository gate
docs/                           Requirement traceability and ADRs
```

## Local verification

Required tools: Go 1.23+, Python 3.12+, Rust 1.82+, and PyYAML.

```bash
python3 -m pip install pyyaml
python3 scripts/verify_repo.py

cd apps/core/svc-tenancy
go test ./...

cd ../svc-provisioning
go test ./...

cd ../../workers/campaign-worker
python3 -m pip install -e '.[test]'
PYTHONPATH=. python3 -m pytest

cd ../studio-worker
python3 -m pip install -e '.[test]'
PYTHONPATH=. python3 -m pytest

cd ../../../libs-rust/tb-adapter
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
- Reconcile provider cost with `usage.events` and publish M1 SLO dashboards.

See [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md) and [`docs/m1/REQUIREMENTS.md`](docs/m1/REQUIREMENTS.md) for requirement-level status.
