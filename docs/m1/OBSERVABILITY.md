# M1 observability contract

Status: **Core, campaign, Studio, and synthetic-probe instrumentation implemented; image publication, operator configuration, and live cluster validation are still required**. The JSON dashboard and PrometheusRule in `monitoring/m1/` are reviewable definitions, not evidence that an SLO has been met.

## Required measurements

Names and label values below form the query contract used by the M1 dashboard and alerts. Do not rename one side without changing and validating the other.

| Metric | Type | Required labels | Meaning |
|---|---|---|---|
| `volumeos_http_requests_total` | counter | `service`, `method`, `route`, `code` | Completed Core API requests. `route` is a low-cardinality route template, never a raw URL. Only `/v1/` traffic is counted; health and scrape requests are excluded. `service=svc-tenancy` feeds the Core API SLO. |
| `volumeos_campaign_cycle_dispatches` | gauge | `tenant_id`, `campaign_id`, `cycle_id`, `channel`, `result` | Reconciled attempted dispatches for one campaign cycle. `result` is `success` or `failure`. Activities write absolute values, making retries safe. Queries take `max` per cycle/channel/result across worker replicas before summing channels. |
| `volumeos_campaign_cycle_completed_timestamp_seconds` | gauge | `tenant_id`, `campaign_id`, `cycle_id` | Latest reconciliation timestamp. SLA alerts page only for cycles reconciled within the last hour, avoiding indefinite alerts for historical cycles. |
| `volumeos_campaign_approvals_total` | counter | `result` | Approval decisions. `result` is `approved`, `rejected`, or `timeout`. |
| `volumeos_campaign_dispatch_duration_seconds` | histogram | `channel`, `result` | Actual provider-call latency for an approved request; idempotent replays and policy denials do not create provider-latency observations. |
| `volumeos_studio_packs_total` | counter | `result` | Terminal Studio packs. `result` is `delivered`, `rejected`, or `failed`; QA timeout is `failed`. |
| `volumeos_studio_pack_duration_seconds` | histogram | `result` | End-to-end Studio pack duration from accepted intake to terminal state. |
| `probe_success` | gauge | `journey` | Latest synthetic-probe result. M1 requires `journey=login` and `journey=send`; `journey=pay` is an M2 requirement. |
| `probe_duration_seconds` | gauge | `journey` | Latest synthetic journey duration, recorded for both success and failure. |

Temporal workflows do not emit metrics during replay. Campaign and Studio emit operational side effects through activities. Workflow code catches metric-activity failures so observability cannot void an already-dispatched campaign, reverse settlement, or change a Studio terminal outcome; these failures are logged for operator investigation. Provider costs continue to reconcile through the tenant/channel/campaign usage record and `usage.events`; a Grafana total is not a financial ledger. NGN amounts remain minor units.

Never put recipient addresses, suppression digests, access tokens, provider secrets, object contents, or raw social identifiers in metric labels. Tenant, campaign, and cycle labels are restricted to the per-cycle operational series; aggregate fleet alerts must drop them. Probe labels contain only the bounded `journey` value, and failure logs contain only the journey and exception type.

## SLO calculations

- **Core API availability — NFR-2:** non-5xx responses divided by all responses for `svc-tenancy`; target **99.5% per calendar month**. The dashboard also provides a rolling 30-day view. A monthly report must state the exact calendar-month window and query export. Prometheus retention is 45 days so a 31-day month remains queryable while its report is produced; longer-term reporting requires the operator’s metrics export/retention process.
- **Campaign dispatch — NFR-2/BR-18:** successful final dispatch attempts divided by all final attempts for each `tenant_id`, `campaign_id`, and `cycle_id`; target **99% per cycle**. A tenant-wide aggregate cannot replace a failing individual cycle.
- **Synthetic journeys — OBS-6:** login and send probes run every **five minutes**. Login performs client-credentials OIDC authentication followed by an authorized tenant-scoped Core API read. Send must return a completed synthetic provider receipt. Pay is not an M1 acceptance substitute; it enters with M2 billing.
- **Operational visibility:** approval outcomes, dispatch latency, Studio outcomes, and Studio turnaround support M1 operations but do not redefine the two NFR-2 availability targets.

## Synthetic configuration boundary

The probe is included only in the dev/stage overlays and runs as one replica in staging. Production inclusion is rejected by the application configuration. Private configuration must supply these resources in `workers` before the Deployment can become ready:

| Resource | Keys | Requirement |
|---|---|---|
| ConfigMap `synthetic-probe` | `tenant-id`, `oidc-token-url`, `core-api-url`, `send-url` | Dedicated synthetic tenant; HTTP(S) URLs without embedded credentials. `core-api-url` normally targets the internal tenancy Service. |
| Secret `synthetic-probe` | `client-id`, `client-secret`, `service-token` | Dedicated least-privilege OIDC client and internal service token of at least 32 characters. Inject through the approved secret pipeline; never commit values. |

`send-url` must identify a **dedicated dev/stage, pre-approved provider-sandbox endpoint**. That endpoint must execute a controlled synthetic send and return HTTP success with this JSON contract:

```json
{"status":"completed","synthetic":true,"receipt_id":"provider-receipt-id"}
```

Health responses, `accepted`, `queued`, missing receipts, and non-synthetic responses fail the probe. The repository does **not** currently implement that provider-sandbox endpoint or supply its credentials. This remains an operator/provider integration gap, not a passing probe. Do not point `send-url` at a generic campaign endpoint to bypass human approval.

## Activation boundary

Changed Core, campaign, and Studio Deployments reference `0.2.0`; the new probe references `0.1.0`. These tags must be built and published before deployment; this source change does not claim the images already exist.

Before treating `monitoring/m1/kustomization.yaml` as active:

1. Build and publish the pinned images, supply the private probe ConfigMap/Secret and dedicated sandbox endpoint, then render and sync dev/stage.
2. Confirm the Core, campaign, Studio, and synthetic ServiceMonitors discover their internal endpoints. No worker or probe metrics port has public ingress.
3. Apply the M1 monitoring resources in staging and verify every query against live series. `absent()` alerts intentionally fail closed when Core or probe series disappear.
4. Test alert delivery to the on-call WhatsApp receiver. No receiver is committed to the public repository.
5. Attach query exports, dashboard evidence, alert tests, five-minute probe coverage, and the monthly report to acceptance checks `OBS-001` and `OBS-002` with `scripts/staging_acceptance.py`.

A dashboard with no data, a zero-request ratio, a health-check substitute, or a manually written success value is not passing evidence.
