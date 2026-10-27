# M1 staging acceptance runbook

This runbook produces evidence; it does not declare acceptance. M1 is accepted only when the fail-closed verifier passes against a real staging run attached to the reviewed source revision. Local unit tests, rendered manifests, and dashboard definitions are not substitutes.

## 1. Prepare a secret-free run

Prerequisites:

- A prod-shaped staging cluster and an explicitly selected kubeconfig context.
- Read-only kubectl access for preflight; separate authorized access for the drills.
- Real provider sandboxes for email, SMS, WhatsApp, and social. Customer social OAuth must already be connected; VolumeOS must not invent or delete it.
- The private `volumeos-tenant-secrets` Dapr component and tenant-scoped Postiz keys.
- A reviewed ComfyUI deployment, model licence, and GPU capacity.
- Built and published `0.2.0` Core/campaign/Studio and `0.1.0` synthetic-probe images, plus the M1 monitoring resources. Do not activate alerts against placeholder metrics.
- A dedicated synthetic tenant and least-privilege OIDC client, the private `workers/synthetic-probe` ConfigMap and Secret, and a pre-approved provider-sandbox send endpoint returning a completed synthetic receipt. A health endpoint or generic campaign route is not a substitute; see `docs/m1/OBSERVABILITY.md`.

Copy `provider-config.example.json` to a private operator location and replace every placeholder. The file identifies configuration only; it must not contain API keys, tokens, passwords, recipient addresses, or connection URLs with credentials.

```bash
export SOURCE_REVISION='<full-40-character-reviewed-commit>'
export RUN_DIR="$HOME/volumeos-evidence/m1-$(date -u +%Y%m%dT%H%M%SZ)"

python3 scripts/staging_acceptance.py preflight \
  --environment stage \
  --context '<explicit-staging-context>' \
  --source-revision "$SOURCE_REVISION" \
  --provider-config "$HOME/volumeos-private/stage-providers.json" \
  --output "$RUN_DIR"
```

Preflight is read-only. It checks provider declarations, cluster access, required deployments (including Studio and the synthetic probe), secret names/keys in their actual namespaces, the Dapr tenant store, GPU capacity, and Core/worker/probe scrape resources. It does not output secret values. A nonzero exit means acceptance is blocked; fix the actual prerequisite rather than editing the JSON result.

## 2. Execute and preserve evidence

Use a dedicated test tenant and synthetic recipients. Follow provider and customer authorization requirements. Record tenant, campaign, cycle, workflow, provider-request, and object identifiers, but redact tokens, recipient addresses, suppression secrets, and personal data before adding artifacts. Review every artifact before recording it; the script hashes files but cannot determine whether a screenshot or log contains sensitive data.

Record each result separately. A passed result requires at least one artifact:

```bash
python3 scripts/staging_acceptance.py record \
  --output "$RUN_DIR" --check MSG-004 --status passed \
  --notes '<sanitized drill summary>' \
  --evidence /secure/reviewed/kill-switch-drill.json
```

Use `--status failed` or `--status blocked` with explanatory notes when appropriate. A later record supersedes the current result and retains the earlier result in history. Never mark a check passed merely because a local test passed.

### Required drills

| Check | Required procedure and pass condition |
|---|---|
| `ONB-001` | Onboard a new staging client through Keycloak, tenancy, ListMonk, existing Postiz OAuth validation, activation, and welcome. Compare contract/signup and completed-onboarding business timestamps; finish within one business day. |
| `ONB-002` | Inject a failure after each owned saga step. Verify reverse compensation and audit events. A reused Keycloak client must survive; a newly created orphan must be removed. Customer-owned Postiz integrations must receive no delete operation. |
| `MSG-001` | Run Content Cadence through email, SMS, WhatsApp, and social with explicit approval. Reconcile provider receipts/webhooks and calculate success for each campaign cycle; every cycle must meet 99%. |
| `MSG-002` | Attempt dispatch before approval, after rejection, and after approval timeout. All must produce zero provider calls. |
| `MSG-003` | Add a synthetic suppressed recipient, restart the relevant worker to defeat process-local state, and retry. Persistent PostgreSQL policy must deny every new provider call. Remove suppression only through the authorized path. |
| `MSG-004` | Suspend a tenant during an active campaign. Later calls across every channel must be denied, with suspension-to-enforcement no more than five minutes. Verify the provider audit shows zero later sends. |
| `MSG-005` | Replay the same idempotency key/body and confirm the stored result with no duplicate provider call or usage event. Replay the key with different content and expect conflict. Attempt cross-tenant reads/writes and expect denial. |
| `STU-001` | Run the pinned graph on a real GPU with a reviewed model. Verify output count, 1080×1080 WhatsApp JPEG, 1000×1000 Jumia JPEG, tenant prefixes, hashes, and workflow/provider identifiers. |
| `STU-002` | Reject one generated asset with a reason. Verify it never appears in delivery prefixes or client delivery, then approve a separate valid pack to prove the normal gate still works. |
| `STU-003` | Measure signup to approved first-report delivery. It must be no more than 48 hours; retain all intermediate timestamps. |
| `SEC-001` | Verify Keycloak MFA and edge OIDC. Expired, invalid, wrong-audience, and wrong-tenant tokens must fail. Direct public service access must not bypass APISIX. Never attach raw tokens. |
| `SEC-002` | Replace Wazuh bootstrap credentials/certificates through the reviewed secret pipeline, activate deliberately, then run all four purple-team scenarios. At least three must produce mapped alerts. |
| `DR-001` | Perform a dated restore drill for PostgreSQL, MinIO, Keycloak, and cluster state into an isolated recovery target. Measure last-recoverable-write age (RPO ≤15 minutes) and declaration-to-validated-service time (RTO ≤4 hours). Validate tenant/RLS and object contents, schedule the next quarterly drill, and destroy the isolated copy safely under the operator’s data-retention process. TigerBeetle joins in M2. |
| `OBS-001` | Verify every dashboard query against live series, test absent-metric and burn/SLA alerts, and export the monthly report. Use exact calendar-month boundaries for Core API 99.5%; calculate dispatch separately for every cycle at 99%. |
| `OBS-002` | Show login and send probes executing every five minutes and alerts reaching the on-call WhatsApp receiver. Publish the monthly report. Do not substitute the M2 pay journey. |
| `COST-001` | Reconcile provider charges to tenant/channel/campaign usage in original currency and NGN minor units where applicable. Explain every variance and obtain operator sign-off. This is showback, not the M2 financial ledger. |

## 3. Verify the bundle

```bash
python3 scripts/staging_acceptance.py verify --output "$RUN_DIR"
```

The verifier exits nonzero unless:

1. Preflight passed for the same environment, context, and source revision.
2. Every check in `docs/m1/acceptance/checks.json` is currently passed.
3. Every passed check has evidence and every current artifact matches its recorded SHA-256.

On success, `run.json` changes from `incomplete` to `accepted` and `verification.json` records the result. Store the whole directory in the operator’s access-controlled evidence system. A checksum detects accidental or later content changes; it is not a digital signature. Use the evidence system’s immutable retention/signing controls where required.

## 4. SLO and recovery reporting

The monthly SLO report must include:

- Exact UTC and Africa/Lagos reporting boundaries.
- Reviewed source/image revisions and relevant configuration changes.
- Core API numerator, denominator, availability, and 99.5% result.
- Every campaign cycle’s successful/total dispatches and 99% result; no fleet-only average.
- Five-minute login/send probe coverage, failures, and alert deliveries.
- Incidents, excluded periods with justification, error-budget consumption, and corrective actions.

The recovery report must separately state RPO and RTO measurements and any data loss. “Backup exists” or “restore command succeeded” without application/tenant validation is not passing evidence.

## 5. Remaining boundaries

The public monitoring resources are not automatically synced. Apply them only after deploying the instrumented images, configuring the real synthetic sandbox journey, and validating the activation boundary in `docs/m1/OBSERVABILITY.md`:

```bash
kubectl --context '<explicit-staging-context>' apply -k monitoring/m1
```

Wazuh remains inactive until its private secret overlay is approved. M2 payment probes, TigerBeetle restore, live billing, M3 analytics, and M4 hardening remain outside M1 acceptance.
