# VolumeOS

Open-source-first operating platform for the Naira Volume Playbook: multi-tenant service delivery, prepaid operations, durable workflows, and geospatial market intelligence for Nigerian MSMEs.

This repository currently implements the **M0 foundation and a tenancy/campaign vertical slice**. It does not claim that the later money, lakehouse, AI, or operator-console milestones are complete.

## Implemented

- **Tenancy (Go):** tenant → workspace → subscription model for offers 01–08; validated lifecycle transitions; optimistic concurrency; outbound-messaging kill switch with an audit reason.
- **Isolation and identity:** RS256 JWT validation against Keycloak JWKS, issuer/audience/expiry enforcement, `tenant_id` on every scoped request, role checks, PostgreSQL row-level security, and cross-tenant tests. Header authentication is a development-only opt-in and is rejected in production.
- **Persistence:** PostgreSQL migrations executed under an advisory lock, per-tenant transactions, no derived financial balances in PostgreSQL.
- **Campaign orchestration (Python/Temporal):** draft → explicit human approval signal → reserve → dispatch → post, with a 24-hour approval timeout and void-on-failure compensation. Activities use real HTTP endpoints, idempotency keys, tenant scope, retries, and heartbeats; there are no simulated provider successes.
- **Money contract (Rust):** deterministic two-phase reserve/post/void state machine with replay and idempotency-conflict tests. The live TigerBeetle transport is an M2 deliverable.
- **Event contracts:** Protobuf envelopes and usage, money, lead, message-status, and audit-publish records; `money.events` is explicitly a mirror, not a ledger.
- **GitOps:** pinned Argo CD applications, Dapr mTLS, APISIX + open-appsec, Strimzi Kafka and seven topics, PostgreSQL, Redis, MinIO buckets, Keycloak, OpenSearch, Temporal, observability, Kubecost, and OpenCTI.
- **Verification:** Go, Python, and Rust tests; static manifest/source checks; CI for tests, lint, and Kustomize rendering.

## Intentionally not represented as complete

ListMonk, Postiz, and Wazuh are not deployed by the app-of-apps yet because the supplied scaffold referenced non-resolvable chart hosts. Their packaging must be selected, licence-reviewed, pinned, and tested before M1 rather than silently substituted with unreviewed charts. The APISIX-to-Keycloak OIDC plugin secret, TigerBeetle transport, Flink/Spark/Sedona/Ray workloads, DataFusion service, and console remain milestone work. The Rust reservation book is a contract model, not a running TigerBeetle adapter.

## Repository layout

```text
apps/core/svc-tenancy/          Go API, OIDC, PostgreSQL, migrations, tests
apps/workers/campaign-worker/   Temporal workflow, activities, tests
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

cd ../../workers/campaign-worker
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

See [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md) for requirement-level status.
