# M1 observability contract

Status: **specified; instrumentation and live cluster validation are still required**. The JSON dashboard and PrometheusRule in `monitoring/m1/` are reviewable definitions, not evidence that an SLO has been met.

## Required measurements

Services must expose Prometheus metrics on their existing scrape endpoint. Names and label values below form the query contract used by the M1 dashboard and alerts. Do not rename one side without changing and validating the other.

| Metric | Type | Required labels | Meaning |
|---|---|---|---|
| `volumeos_http_requests_total` | counter | `service`, `method`, `route`, `code` | Completed Core API requests. `route` must be a low-cardinality route template, never a raw URL. `service=svc-tenancy` feeds the Core API SLO. |
| `volumeos_campaign_cycle_dispatches` | gauge | `tenant_id`, `campaign_id`, `cycle_id`, `channel`, `result` | Final number of attempted dispatches for one completed campaign cycle. `result` is `success` or `failure`; write the completed counts only after provider reconciliation. |
| `volumeos_campaign_approvals_total` | counter | `result` | Approval decisions. `result` is `approved`, `rejected`, or `timeout`. |
| `volumeos_campaign_dispatch_duration_seconds` | histogram | `channel`, `result` | Provider dispatch latency for an approved request. |
| `volumeos_studio_packs_total` | counter | `result` | Completed Studio packs. `result` is `delivered`, `rejected`, or `failed`. |
| `volumeos_studio_pack_duration_seconds` | histogram | `result` | End-to-end Studio pack duration from accepted intake to terminal state. |
| `probe_success` | gauge | `journey` | Standard synthetic-probe result. M1 requires `journey=login` and `journey=send`; `journey=pay` is an M2 requirement. |

Provider costs must continue to reconcile through the tenant/channel/campaign usage record and `usage.events`; a Grafana total is not a financial ledger. NGN amounts remain minor units.

Never put recipient addresses, suppression digests, access tokens, provider secrets, object contents, or raw social identifiers in metric labels. Tenant, campaign, and cycle labels are restricted to the per-cycle operational series; aggregate fleet alerts must drop them.

## SLO calculations

- **Core API availability — NFR-2:** non-5xx responses divided by all responses for `svc-tenancy`; target **99.5% per calendar month**. The dashboard also provides a rolling 30-day view. A monthly report must state the exact calendar-month window and query export. Prometheus retention is 45 days so a 31-day month remains queryable while its report is produced; longer-term reporting requires the operator’s metrics export/retention process.
- **Campaign dispatch — NFR-2/BR-18:** successful final dispatch attempts divided by all final attempts for each `tenant_id`, `campaign_id`, and `cycle_id`; target **99% per cycle**. A tenant-wide aggregate cannot replace a failing individual cycle.
- **Synthetic journeys — OBS-6:** login and send probes run every **five minutes**. Pay is not an M1 acceptance substitute; it enters with M2 billing.
- **Operational visibility:** approval outcomes, dispatch latency, Studio outcomes, and Studio turnaround support M1 operations but do not redefine the two NFR-2 availability targets.

## Activation boundary

The current Go and Python implementations do not yet emit all metrics above. Before applying `monitoring/m1/kustomization.yaml`:

1. Instrument the services and synthetic probes, add unit tests for label/result handling, and build new pinned images.
2. Confirm the ServiceMonitor/scrape configuration discovers those endpoints.
3. Apply the M1 monitoring resources in staging and verify every query against live series. `absent()` alerts intentionally fail closed when core or probe series disappear.
4. Test alert delivery to the on-call WhatsApp receiver. No receiver is committed to the public repository.
5. Attach query exports, dashboard evidence, alert tests, and the monthly report to acceptance checks `OBS-001` and `OBS-002` with `scripts/staging_acceptance.py`.

A dashboard with no data, a zero-request ratio, or a manually written success value is not passing evidence.
