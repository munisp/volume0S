# M4 security operations

Status: source configuration; live Wazuh, OpenCTI, OpenSearch, paging, and restore acceptance are not claimed.

## Detection pipeline

1. Services emit structured security events with hashed tenant/actor identifiers and no prompts, credentials, raw subject PII, payment references, or suppression secrets.
2. Wazuh applies `security/secops/wazuh-local-rules.xml`; OpenSearch runs the corresponding query monitor. Every alert retains its MITRE mapping and evidence query.
3. The OpenCTI connector imports only sanitized reports. Its example endpoint is deliberately non-routable and its credentials are secret references.
4. Critical detections page the security on-call, preserve evidence, and freeze tenant provider egress where appropriate. Safety controls are never disabled to recover availability.

## Error budgets

`security/secops/slo.yaml` defines 30-day objectives and fast/slow burn response. Exhaustion freezes feature rollout, not tenant isolation, financial integrity, approval, redaction, or evidence capture.

## Quarterly restore drill

Run `scripts/restore_drill.py preflight --config <drill-config.json>` before the isolated restore. After restoration, capture the signed evidence manifest and run `scripts/restore_drill.py verify --manifest <manifest.json>`. A successful drill requires isolated credentials/network, PostgreSQL and TigerBeetle integrity checks, object digest checks, RLS tenant probes, Temporal workflow recovery, DSAR deletion re-application, measured RPO/RTO, and named reviewers. Timestamps or backup existence alone are not evidence of restorability.

Restored backups may contain records deleted after the backup point. Before reopening traffic, replay the deletion tombstone log and acknowledge every affected target; never treat a restored copy as proof that deletion obligations disappeared.
