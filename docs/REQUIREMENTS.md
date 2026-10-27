# M0 requirement traceability

Status terms: **tested** = automated local verification; **configured** = manifests/contracts present but cluster acceptance required; **later** = intentionally outside the current vertical slice.

| Requirement | Implementation | Status |
|---|---|---|
| TEN-1 | `tenants`, `workspaces`, `subscriptions`; offer/tier constraints and foreign keys | tested / configured |
| TEN-2 | Keycloak roles plus endpoint authorization middleware | tested |
| TEN-3 | Explicit lifecycle map, optimistic version checks, lifecycle events | tested |
| TEN-4 | OIDC `tenant_id`, scoped store calls, PostgreSQL RLS, isolation tests | tested / configured |
| TEN-5 / MSG-7 | Audited outbound-suspension flag; dispatch-path integration is required for the timed drill | tested / configured |
| IDN-1/2 | RS256 JWKS, issuer, audience, expiry; Dapr mTLS chart configuration | tested / configured |
| IDN-3/4/5 | Keycloak deployed; realm MFA, user lifecycle, and claim mapper require cluster setup | configured |
| EDGE-1/6 | ClusterIP service, APISIX route, Dapr, default-deny baseline | configured |
| EDGE-2 | Service validates OIDC; APISIX plugin requires SOPS-delivered client configuration | configured |
| EDGE-3/4/5 | open-appsec/APISIX deployed; rate/replay/admin/WAF acceptance remains | configured |
| FLW-2/5/6 | Durable campaign, approval signal/timeout, idempotent HTTP activities, retries, compensation | tested / configured |
| BIL-1/2 | u128 minor-unit reservation contract; live TigerBeetle and all balance reads reserved for M2 | tested / later |
| STR-1/2 | Protobuf contracts; Kafka topics, partitions, RF, and retention | configured |
| STR-3/4 | Fluvio pinned from source; connectors, SmartModules, Debezium and replay drills remain | configured / later |
| OBS-1/2/5 | Prometheus/Grafana/OpenSearch/Kubecost applications and service scrape annotations; M1 SLO dashboard/alert sources and metric contract added, but instrumentation and live SLO evidence remain open | configured / acceptance required |
| OBS-6, NFR-2/3/4 | Requirement-mapped M1 staging preflight, evidence verifier, and operational runbook; real probes, alerts, SLO reports, and restore drills require staging | tested tooling / acceptance required |
| NFR-7 | TLS/mTLS components and non-root containers; secret and certificate drills remain | configured |
| NFR-12 | CI tests/lint plus money-state tests; connector and chaos gates expand by milestone | tested / configured |

The data-plane documents (bronze/silver/gold, Flink, Spark, Sedona, Ray, DataFusion) define M3. No tables or jobs are mocked here as operational.
