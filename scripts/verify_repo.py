#!/usr/bin/env python3
"""Repository-level M0/M1 checks; no Kubernetes cluster credentials required."""
from pathlib import Path
import json
import re
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
errors = []
yaml_files = [p for p in ROOT.rglob("*.yaml") if ".git" not in p.parts]
for path in yaml_files:
    try:
        list(yaml.safe_load_all(path.read_text()))
    except Exception as exc:
        errors.append(f"{path.relative_to(ROOT)}: invalid YAML: {exc}")

# The ComfyUI graph is a pre-render template containing {{image_count}} and is
# validated by its Studio tests after substitution rather than as raw JSON.
json_files = [
    p for p in ROOT.rglob("*.json")
    if ".git" not in p.parts and "node_modules" not in p.parts
    and p.name != "shopfront-img2img-v1.json" and not p.name.startswith("tsconfig")
]
for path in json_files:
    try:
        json.loads(path.read_text())
    except Exception as exc:
        errors.append(f"{path.relative_to(ROOT)}: invalid JSON: {exc}")

for path in ROOT.rglob("*"):
    if path.is_file() and ".git" not in path.parts and path.suffix in {".yaml", ".yml", ".go", ".py", ".rs", ".md", ".toml"}:
        text = path.read_text(errors="ignore")
        for forbidden in ("YOUR" + "_ORG", "charts.chartmuseum" + ".local", "targetRevision:" + " HEAD"):
            if forbidden in text:
                errors.append(f"{path.relative_to(ROOT)}: forbidden placeholder {forbidden}")

apps = ROOT / "bootstrap/argocd/apps"
for path in apps.glob("*.yaml"):
    doc = yaml.safe_load(path.read_text())
    if not doc.get("spec", {}).get("source", {}).get("targetRevision"):
        errors.append(f"{path.relative_to(ROOT)}: unpinned source revision")

required_m1_apps = {"listmonk", "postiz", "wazuh"}
missing_apps = sorted(name for name in required_m1_apps if not (apps / f"{name}.yaml").exists())
if missing_apps:
    errors.append(f"missing M1 applications: {', '.join(missing_apps)}")
else:
    listmonk = yaml.safe_load((apps / "listmonk.yaml").read_text())
    if listmonk["spec"]["source"].get("targetRevision") != "1.2.3":
        errors.append("ListMonk chart must remain at the reviewed 1.2.3 release")
    wazuh = yaml.safe_load((apps / "wazuh.yaml").read_text())
    if "automated" in wazuh["spec"].get("syncPolicy", {}):
        errors.append("Wazuh must not auto-sync before its private secret overlay")
    if wazuh["spec"]["source"].get("targetRevision") != "v4.14.9":
        errors.append("Wazuh must use the reviewed v4.14.9 source")

prometheus_app_text = (apps / "prometheus-grafana.yaml").read_text()
if "retention: 45d" not in prometheus_app_text:
    errors.append("Prometheus retention must cover calendar-month SLO reporting plus lag")

postiz_path = ROOT / "bootstrap/m1/postiz/deployment.yaml"
if postiz_path.exists():
    postiz_text = postiz_path.read_text()
    if "ghcr.io/gitroomhq/postiz-app:v1.3.0" not in postiz_text:
        errors.append("Postiz image differs from the reviewed v1.3.0 package")
    for required in ("secretRef: {name: postiz}", "type: ClusterIP", 'DISABLE_REGISTRATION, value: "true"'):
        if required not in postiz_text:
            errors.append(f"Postiz packaging is missing {required}")

studio_graph = ROOT / "apps/workers/studio-worker/workflows/shopfront-img2img-v1.json"
if studio_graph.exists():
    graph_text = studio_graph.read_text()
    for required in ('"CheckpointLoaderSimple"', '"RepeatLatentBatch"', '"SaveImage"', '{{image_count}}'):
        if required not in graph_text:
            errors.append(f"Studio graph is missing {required}")

for env in ("dev", "stage", "prod"):
    path = ROOT / f"deploy/overlays/{env}/kustomization.yaml"
    doc = yaml.safe_load(path.read_text())
    resources = doc.get("resources", [])
    for resource in resources:
        if not (path.parent / resource).exists():
            errors.append(f"{path.relative_to(ROOT)}: missing resource {resource}")
    if "../../../apps/core/svc-provisioning/k8s" not in resources:
        errors.append(f"{path.relative_to(ROOT)}: svc-provisioning is not deployed")
    if "../../../apps/core/svc-orchestrator/k8s" not in resources:
        errors.append(f"{path.relative_to(ROOT)}: svc-orchestrator is not deployed")
    if "../../../apps/core/svc-conversations/k8s" not in resources:
        errors.append(f"{path.relative_to(ROOT)}: svc-conversations is not deployed")
    if "../../../apps/workers/offer-worker/k8s" not in resources:
        errors.append(f"{path.relative_to(ROOT)}: offer-worker is not deployed")

messaging_migration = ROOT / "apps/core/svc-tenancy/migrations/002_messaging.sql"
migration_text = messaging_migration.read_text()
for required in ("CREATE TABLE IF NOT EXISTS suppressions", "CREATE TABLE IF NOT EXISTS dispatch_results", "ENABLE ROW LEVEL SECURITY"):
    if required not in migration_text:
        errors.append(f"messaging migration is missing {required}")
onboarding_text = (ROOT / "apps/workers/campaign-worker/onboarding.py").read_text()
if '"undo:postiz"' in onboarding_text:
    errors.append("customer-owned Postiz integrations must not be compensated")
if "postiz_integration_id" not in onboarding_text:
    errors.append("onboarding must validate an explicit Postiz integration")

acceptance_catalog_path = ROOT / "docs/m1/acceptance/checks.json"
acceptance_catalog = json.loads(acceptance_catalog_path.read_text())
acceptance_ids = [check["id"] for check in acceptance_catalog["checks"]]
if len(acceptance_ids) != len(set(acceptance_ids)):
    errors.append("M1 acceptance catalog contains duplicate check IDs")
for required_check in ("ONB-001", "ONB-002", "MSG-001", "MSG-003", "MSG-004", "STU-001", "DR-001", "OBS-001", "OBS-002", "COST-001"):
    if required_check not in acceptance_ids:
        errors.append(f"M1 acceptance catalog is missing {required_check}")
acceptance_script = (ROOT / "scripts/staging_acceptance.py").read_text()
for required in ('"passed" and not args.evidence', 'preflight.get("status") != "passed"', 'digest(path) != artifact.get("sha256")'):
    if required not in acceptance_script:
        errors.append(f"staging acceptance no longer fails closed: {required}")

monitoring_dir = ROOT / "monitoring/m1"
rule_path = monitoring_dir / "prometheusrule.yaml"
rule = yaml.safe_load(rule_path.read_text())
rule_text = rule_path.read_text()
for required in (
    "0.072", "0.03", "volumeos:campaign_dispatch_success_ratio:cycle < 0.99",
    'absent(volumeos_http_requests_total{service="svc-tenancy"})',
    'absent(probe_success{journey="login"}) or absent(probe_success{journey="send"})',
    "volumeos_campaign_cycle_completed_timestamp_seconds", "> time() - 3600",
    "max by (tenant_id, campaign_id, cycle_id, channel, result)",
    "sum by (tenant_id, campaign_id, cycle_id)",
):
    if required not in rule_text:
        errors.append(f"M1 SLO rule is missing {required}")
if rule["metadata"]["name"] != "volumeos-m1-slo":
    errors.append("M1 PrometheusRule name differs from staging preflight")
dashboard = json.loads((monitoring_dir / "grafana-dashboard.json").read_text())
if dashboard.get("uid") != "volumeos-m1-slo":
    errors.append("M1 Grafana dashboard UID is missing or incorrect")
dashboard_text = json.dumps(dashboard)
for required in (
    "0.995", "volumeos:campaign_dispatch_success_ratio:cycle", "probe_success",
    "volumeos_studio_packs_total", "max by (tenant_id, campaign_id, cycle_id, channel, result)",
    "sum by (channel, result)",
):
    if required not in dashboard_text:
        errors.append(f"M1 dashboard is missing {required}")
monitoring_kustomization_path = monitoring_dir / "kustomization.yaml"
monitoring_kustomization = yaml.safe_load(monitoring_kustomization_path.read_text())
for resource in monitoring_kustomization.get("resources", []):
    if not (monitoring_dir / resource).is_file():
        errors.append(f"monitoring/m1: missing resource {resource}")
for generator in monitoring_kustomization.get("configMapGenerator", []):
    for entry in generator.get("files", []):
        source = entry.split("=", 1)[-1]
        if not (monitoring_dir / source).is_file():
            errors.append(f"monitoring/m1: missing dashboard source {source}")

# M1 emission contracts must stay aligned with the dashboard and alert queries.
instrumentation_contracts = {
    "apps/core/svc-tenancy/metrics.go": ("volumeos_http_requests_total", 'service=\\"svc-tenancy\\"'),
    "apps/workers/campaign-worker/metrics.py": (
        "volumeos_campaign_approvals_total", "volumeos_campaign_cycle_dispatches",
        "volumeos_campaign_dispatch_duration_seconds",
        "volumeos_campaign_cycle_completed_timestamp_seconds", "CYCLE_COMPLETED",
    ),
    "apps/workers/studio-worker/metrics.py": (
        "volumeos_studio_packs_total", "volumeos_studio_pack_duration_seconds",
    ),
    "apps/workers/synthetic-probe/main.py": (
        "probe_success", "probe_duration_seconds", "INTERVAL_SECONDS = 300.0",
        'body.get("status") != "completed"', 'body.get("synthetic") is not True',
    ),
}
for relative, required_values in instrumentation_contracts.items():
    path = ROOT / relative
    if not path.is_file():
        errors.append(f"missing M1 instrumentation: {relative}")
        continue
    text = path.read_text()
    for required in required_values:
        if required not in text:
            errors.append(f"{relative}: missing metric/probe contract {required}")

workflow_isolation_contracts = {
    "apps/workers/campaign-worker/main.py": (
        'workflow.logger.warning("approval metric recording failed: %s", exc)',
        'workflow.logger.warning("campaign cycle metric recording failed: %s", exc)',
    ),
    "apps/workers/studio-worker/studio_main.py": (
        'workflow.logger.warning("Studio failure metric recording failed: %s", metric_error)',
        'workflow.logger.warning("Studio outcome metric recording failed: %s", metric_error)',
    ),
}
for relative, required_values in workflow_isolation_contracts.items():
    text = (ROOT / relative).read_text()
    for required in required_values:
        if required not in text:
            errors.append(f"{relative}: metric failure can affect a business outcome; missing {required}")

for relative, image in (
    ("apps/core/svc-tenancy/k8s/deployment.yaml", "ghcr.io/munisp/svc-tenancy:0.2.0"),
    ("apps/core/svc-orchestrator/k8s/deployment.yaml", "ghcr.io/munisp/svc-orchestrator:0.4.0"),
    ("apps/workers/campaign-worker/k8s/deployment.yaml", "ghcr.io/munisp/campaign-worker:0.2.0"),
    ("apps/workers/studio-worker/k8s/deployment.yaml", "ghcr.io/munisp/studio-worker:0.2.0"),
    ("apps/workers/synthetic-probe/k8s/deployment.yaml", "ghcr.io/munisp/synthetic-probe:0.1.0"),
):
    if image not in (ROOT / relative).read_text():
        errors.append(f"{relative}: expected instrumented image {image}")
for env in ("dev", "stage"):
    overlay_text = (ROOT / f"deploy/overlays/{env}/kustomization.yaml").read_text()
    if "../../../apps/workers/synthetic-probe/k8s" not in overlay_text:
        errors.append(f"{env} overlay is missing the M1 synthetic probe")
if "synthetic-probe" in (ROOT / "deploy/overlays/prod/kustomization.yaml").read_text():
    errors.append("synthetic send probe must not be included in production")
worker_monitors = (monitoring_dir / "worker-servicemonitors.yaml").read_text()
for name in ("campaign-worker", "studio-worker", "synthetic-probe"):
    if f"name: {name}" not in worker_monitors:
        errors.append(f"worker ServiceMonitors are missing {name}")
acceptance_preflight = (ROOT / "scripts/staging_acceptance.py").read_text()
for required in ('("workers", "synthetic-probe")', '("servicemonitor", "synthetic-probe")'):
    if required not in acceptance_preflight:
        errors.append(f"staging preflight is missing {required}")

required_topics = {"usage.events", "usage.rollups", "message.status", "census.pings", "lead.events", "money.events", "ai.jobs", "audit.publish"}
text = (ROOT / "bootstrap/platform/kafka-topics.yaml").read_text()
missing = sorted(topic for topic in required_topics if f"name: {topic}" not in text)
if missing:
    errors.append(f"missing Kafka topics: {', '.join(missing)}")

for path in ROOT.rglob("Dockerfile"):
    text = path.read_text()
    if re.search(r"FROM\s+\S+:latest", text):
        errors.append(f"{path.relative_to(ROOT)}: floating base image")

python_tested_images = [ROOT / f"apps/workers/{worker}/Dockerfile" for worker in
                        ("campaign-worker", "studio-worker", "synthetic-probe")]
python_tested_images.extend([
    ROOT / "apps/core/svc-orchestrator/Dockerfile",
    ROOT / "apps/core/svc-conversations/Dockerfile",
    ROOT / "apps/workers/offer-worker/Dockerfile",
])
for dockerfile in python_tested_images:
    text = dockerfile.read_text()
    for required in ("python -m pytest && touch /tests-passed", "COPY --from=test /tests-passed /app/.tests-passed"):
        if required not in text:
            errors.append(f"{dockerfile.relative_to(ROOT)}: runtime image does not require its test stage: {required}")
image_delivery = ROOT / "scripts/m1_image_delivery.py"
image_delivery_text = image_delivery.read_text()
for required in (
    '"build", "--pull=always", "--no-cache"', '("smoke", smoke), ("publish", publish), ("verify", verify)',
    '"--read-only", "--cap-drop=ALL"', 'require_report(output / "smoke.json", "passed"',
    '"inspect", "--format", "{{.Digest}}"', 'tools.remote_digest(ref) != entry["digest"]',
    'org.opencontainers.image.revision=', '"pinned": f"{ref}@{digest}"',
):
    if required not in image_delivery_text:
        errors.append(f"M1 image delivery is missing fail-closed contract {required}")

# The orchestrator must expose real tenant-scoped Temporal reads and mutations,
# not a second queue database. Studio review remains bound to the exact objects
# and manifest that a human reviewed.
orchestrator = ROOT / "apps/core/svc-orchestrator"
for relative in (
    "main.py", "auth.py", "assets.py", "workflows.py", "pyproject.toml",
    "Dockerfile", "k8s/deployment.yaml", "k8s/kustomization.yaml",
    "tests/test_api.py", "tests/test_assets.py", "tests/test_auth.py",
):
    if not (orchestrator / relative).is_file():
        errors.append(f"apps/core/svc-orchestrator: missing {relative}")
orchestrator_contracts = {
    "auth.py": (
        'header.get("alg") != "RS256"', 'algorithms=["RS256"]',
        'options={"require": ["exp", "tenant_id", "sub"]}', "return Claims(tenant_id, actor, role_set)",
    ),
    "workflows.py": (
        'await handle.query("status")', "WORKFLOW_ID_REUSE_POLICY_REJECT_DUPLICATE",
        'WorkflowId STARTS_WITH', "QUEUE_LIMIT = 100",
    ),
    "main.py": (
        'ConfigDict(extra="forbid"', 'f"campaign:{claims.tenant_id}:{body.campaign_id}"',
        'f"studio:{claims.tenant_id}:{body.pack_id}"', '"approver": claims.actor',
        '"reviewer": claims.actor', 'snapshot.get("manifest_sha256") != body.manifest_sha256',
        'await app.state.qa_assets.validate_manifest(', 'response.headers["Cache-Control"] = "no-store"',
        '"OfferWorkflow", argument, f"offer:{claims.tenant_id}:{body.job_id}", "offers-v1"',
        'artifact.get("sha256") != body.artifact_sha256', '"reviewer": claims.actor',
    ),
    "assets.py": (
        "hmac.compare_digest(actual_sha256, expected_sha256)", 'ResponseCacheControl": "no-store"',
        "expires_seconds < 60 or expires_seconds > 900", 'head.get("ContentType") != "image/png"',
    ),
}
for relative, required_values in orchestrator_contracts.items():
    path = orchestrator / relative
    if not path.is_file():
        continue
    text = path.read_text()
    for required in required_values:
        if required not in text:
            errors.append(f"apps/core/svc-orchestrator/{relative}: missing safety contract {required}")
platform_config = (ROOT / "bootstrap/platform/config.yaml").read_text()
for required in ("name: svc-orchestrator", "https://console.volumeos.example.com", "allow_credential: false"):
    if required not in platform_config:
        errors.append(f"APISIX orchestrator route/CORS is missing {required}")
network_policy = (ROOT / "policies/default-deny-networkpolicy.yaml").read_text()
for required in (
    "name: allow-edge-core-ingress", "values: [svc-tenancy, svc-orchestrator, svc-collections, svc-census-sync, svc-query]",
    "name: allow-orchestrator-dependencies", "{protocol: TCP, port: 7233}", "{protocol: TCP, port: 9000}",
):
    if required not in network_policy:
        errors.append(f"orchestrator NetworkPolicy is missing {required}")
for required in ('"svc-orchestrator"', '("core", "svc-orchestrator")', '("workers", "studio-worker")'):
    if required not in acceptance_preflight:
        errors.append(f"staging preflight is missing orchestrator/object-store gate {required}")
workflow_query_contracts = {
    "apps/workers/campaign-worker/main.py": ("def status(self) -> CampaignStatus:", 'self._status != "awaiting_approval"'),
    "apps/workers/studio-worker/studio_main.py": (
        "def status(self) -> StudioStatus:", "hmac.compare_digest(actual_sha256, review.manifest_sha256)",
        "hmac.compare_digest(review.manifest_sha256, self._manifest_sha256)",
    ),
}
for relative, required_values in workflow_query_contracts.items():
    text = (ROOT / relative).read_text()
    for required in required_values:
        if required not in text:
            errors.append(f"{relative}: missing live query/review contract {required}")

# The M1 console must preserve its authentication, preview-isolation, and
# network-only API boundaries as source evolves.
console = ROOT / "apps/console"
for relative in (
    "package.json", "vite.config.ts", "src/App.tsx",
    "src/lib/api.ts", "src/lib/auth.ts", "src/lib/config.ts", "src/sw.ts",
    "src/sections/OfferOperationsSection.tsx", "public/runtime-config.json", "scripts/generate-icons.mjs",
):
    if not (console / relative).is_file():
        errors.append(f"apps/console: missing {relative}")
console_contracts = {
    "src/App.tsx": (
        "setCampaigns([])", "setPacks([])", "key={campaign.id}",
        "No live campaign cycles loaded", "No live Studio packs loaded",
        "Illustrative preview queue · no live actions", "previouslyFocused?.focus()",
        "Promise.all([api.campaigns(), api.studioPacks()])", "await refreshWorkflows()",
        "qaManifest === currentPack.manifestSha256", "qaAssets.every(asset => loadedQaAssets[asset.id])",
        "qaTargetAssetCount, qaReloadToken]", "Refresh signed asset URLs",
        "disabled={!connected || busy || pack.status !== 'awaiting_qa' || !qaReviewReady}",
    ),
    "src/lib/api.ts": (
        "cache: 'no-store'", "credentials: 'omit'", "AbortSignal.timeout(15000)",
        "headers.Authorization = `Bearer ${accessToken}`",
    ),
    "src/lib/auth.ts": (
        "response_type: 'code'", "window.sessionStorage", "automaticSilentRenew: false",
    ),
    "src/lib/config.ts": ("cache: 'no-store'", "conversationsApiUrl"),
    "src/sections/OfferOperationsSection.tsx": (
        "selectedJob.artifact!.sha256", "selectedJob.status !== 'awaiting-review'",
        "draft.status !== 'approved'", "No preview conversation data is substituted",
        "The API never accepts a tenant ID from this form",
    ),
    "vite.config.ts": ("strategies: 'injectManifest'", "globIgnores: ['runtime-config.json']"),
    "src/sw.ts": ("precacheAndRoute(self.__WB_MANIFEST)",),
}
for relative, required_values in console_contracts.items():
    path = console / relative
    if not path.is_file():
        continue
    text = path.read_text()
    for required in required_values:
        if required not in text:
            errors.append(f"apps/console/{relative}: missing safety contract {required}")
for path in (console / "src").rglob("*"):
    if path.is_file() and path.suffix in {".ts", ".tsx"}:
        text = path.read_text()
        for forbidden in ("localStorage", "client_secret", "kimi-plugin-inspect-react"):
            if forbidden in text:
                errors.append(f"{path.relative_to(ROOT)}: forbidden console boundary {forbidden}")
runtime_config = json.loads((console / "public/runtime-config.json").read_text())
if any((runtime_config.get("coreApiUrl"), runtime_config.get("orchestratorApiUrl"), runtime_config.get("conversationsApiUrl"), runtime_config["oidc"].get("authority"), runtime_config["oidc"].get("clientId"))):
    errors.append("apps/console: source runtime configuration must not contain deployment endpoints")
console_package = json.loads((console / "package.json").read_text())
if console_package.get("scripts", {}).get("prebuild") != "npm run icons":
    errors.append("apps/console: production build must regenerate PWA icons from reviewed source")
icon_generator = (console / "scripts/generate-icons.mjs").read_text()
for required in ("deflateSync", "crc32", "apple-touch-icon.png", "pwa-192.png", "pwa-512.png", "pwa-maskable-512.png"):
    if required not in icon_generator:
        errors.append(f"apps/console: icon generator is missing {required}")

# M2 source gates: these checks establish wiring and fail-closed contracts only.
# They are not evidence of a live TigerBeetle, Kafka, Flink, or provider cluster.
m2_contracts = {
    "apps/core/svc-billing/k8s/deployment.yaml": (
        "ghcr.io/munisp/svc-billing:0.1.0", "name: volumeos-money", "path: /health/ready",
    ),
    "apps/core/svc-collections/k8s/deployment.yaml": (
        "ghcr.io/munisp/svc-collections:0.1.0", "name: collection-providers", "name: DATABASE_URL",
    ),
    "apps/core/svc-orchestrator/k8s/deployment.yaml": (
        "ghcr.io/munisp/svc-orchestrator:0.4.0", "name: BILLING_SERVICE_URL",
        "name: COLLECTIONS_SERVICE_URL", "name: VOLUMEOS_SERVICE_TOKEN",
    ),
    "apps/workers/billing-worker/k8s/deployment.yaml": (
        "value: billing-v1", "name: DATABASE_URL", "name: DUNNING_CHANNELS",
    ),
    "apps/workers/campaign-worker/k8s/deployment.yaml": (
        "name: BILLING_SERVICE_URL", "key: billing-service-url",
    ),
    "apps/data/usage-rollup/rollup_job.py": (
        "CheckpointingMode.EXACTLY_ONCE", "PARTITION BY event_id",
        "SUM(charge_minor) AS amount_minor", "SUM(cost_minor) AS provider_cost_minor",
    ),
    "apps/data/usage-rollup/dispatcher.py": (
        "group_id=None", "auto_offset_reset=\"earliest\"",
        "WorkflowIDReusePolicy.REJECT_DUPLICATE", "closed rollup changed after posting",
    ),
    "apps/workers/billing-worker/billing_main.py": (
        "class UsageRollupWorkflow:", "def status(self) -> UsageWeekStatus:",
        "WeeklyUsagePosting.from_days(self._days)", "parse_usage_posting(response, value)",
    ),
}
for relative, required_values in m2_contracts.items():
    path = ROOT / relative
    if not path.is_file():
        errors.append(f"M2: missing {relative}")
        continue
    text = path.read_text()
    for required in required_values:
        if required not in text:
            errors.append(f"{relative}: missing M2 contract {required}")
for env in ("dev", "stage", "prod"):
    overlay_text = (ROOT / f"deploy/overlays/{env}/kustomization.yaml").read_text()
    for resource in ("apps/core/svc-billing/k8s", "apps/core/svc-collections/k8s",
                     "apps/workers/billing-worker/k8s", "apps/data/usage-rollup/k8s"):
        if resource not in overlay_text:
            errors.append(f"{env} overlay is missing {resource}")
for required in ("paths: [/v1/collections/webhooks/*]", "/v1/billing/*", "name: volumeos-money"):
    if required not in platform_config:
        errors.append(f"M2 gateway/configuration is missing {required}")
for required in ("name: allow-money-service-ingress", "values: [campaign-worker, billing-worker]",
                 "name: allow-collection-provider-egress"):
    if required not in network_policy:
        errors.append(f"M2 NetworkPolicy is missing {required}")

# M3 source gates: engine ownership, field synchronization, media keys, and
# tenant-safe serving. These checks do not assert a live analytics cluster.
m3_required_files = (
    "apps/data/lakehouse/contracts.py", "apps/data/lakehouse/ingest.py",
    "apps/data/lakehouse/conform.py", "apps/data/lakehouse/gold.py",
    "apps/data/lakehouse/catalog.json", "apps/data/lakehouse/jobs/spark_etl.py",
    "apps/data/streaming/watches.py", "apps/data/geospatial/spatial.py",
    "apps/data/geospatial/sedona_job.py", "apps/data/ray-jobs/registry.py",
    "apps/data/ray-jobs/ray_job.py", "apps/core/svc-query/src/query.rs",
    "apps/core/svc-census-sync/sync.py", "apps/core/svc-census-sync/main.py",
    "apps/core/svc-census-sync/store.py", "apps/core/svc-census-sync/auth.py",
    "apps/core/svc-census-sync/fluvio_sink.py",
    "apps/core/svc-census-sync/migrations/001_census_sync.sql",
    "apps/data/media-security/envelope.py", "apps/data/media-security/vault.py",
    "apps/data/report-search/search.py", "apps/data/report-search/main.py",
    "fluvio/connectors/census-kafka-sink.yaml", "scripts/verify_m3.py",
)
for relative in m3_required_files:
    if not (ROOT / relative).is_file():
        errors.append(f"M3: missing {relative}")

m3_contracts = {
    "apps/data/lakehouse/ingest.py": (
        "self.dedup.contains(self.consumer, envelope.event_id)", "raw_sha256",
        "Transient storage failures propagate for broker retry",
    ),
    "apps/data/lakehouse/conform.py": (
        "PHONE.sub", "hmac.new(self.masking_key", "scoring_eligible", "Sedona/H3 spatial indexer",
    ),
    "apps/data/lakehouse/gold.py": (
        "PII_COLUMNS.intersection(row)", "quality-approved tenant-scoped", "mode=\"append\"",
    ),
    "apps/data/lakehouse/jobs/spark_etl.py": (
        "TRANSFORMS", "row_number().over(dedup)", "quality_decision", "mode(\"append\")",
        "ST_AsText(ST_Point", "volumeos_h3_r9", "def maintenance", "Spark/Delta",
    ),
    "apps/data/streaming/watches.py": ("event_id", "delivery-sla", "send_rate", "cost-breach"),
    "apps/data/geospatial/spatial.py": ("latlng_to_cell", "9", "7", "isochrone", "digital_silence"),
    "apps/data/geospatial/sedona_job.py": ("ST_Contains", "volumeos_h3_r9", "volumeos_h3_r7"),
    "apps/data/ray-jobs/registry.py": ("hmac", "approved", "model_version", "weight_version"),
    "apps/data/ray-jobs/ray_job.py": ("ray.remote", "train_and_review_candidate", "score_approved_batch"),
    "apps/core/svc-query/src/query.rs": (
        "MAX_ROWS: u32 = 1_000", "MAX_DAYS: i64 = 92", "`tenant_id` = '{tenant_id}'",
        "approved client-facing gold mart", "SessionContext",
    ),
    "apps/core/svc-census-sync/sync.py": (
        "MAX_CHUNKS = 64", "MAX_UPLOAD_BYTES = 8 * 1024 * 1024", "hmac.compare_digest",
        "await self.sink.publish", "await self.store.publish", "phone_hash", "del self.chunks",
    ),
    "apps/core/svc-census-sync/main.py": (
        "OIDCAuthenticator", "X-VolumeOS-Device", "claims.device_id != device_id",
        "claims.can_sync()", "CensusSync",
    ),
    "apps/core/svc-census-sync/store.py": (
        "set_config('volumeos.tenant_id'", "ON CONFLICT DO NOTHING", "DELETE FROM census_chunks",
    ),
    "apps/core/svc-census-sync/auth.py": (
        'header.get("alg") != "RS256"', '"device_id"', "field-agent", "algorithms=[\"RS256\"]",
    ),
    "apps/core/svc-census-sync/fluvio_sink.py": ("async_send", "async_wait", "async_flush"),
    "apps/core/svc-census-sync/migrations/001_census_sync.sql": (
        "ENABLE ROW LEVEL SECURITY", "FORCE ROW LEVEL SECURITY", "UNIQUE (tenant_id, event_id)",
    ),
    "apps/data/media-security/envelope.py": (
        "AESGCM", "WrappedKey", "wrapped_dek", "def rotate", "self.provider.unwrap", "associated_data",
    ),
    "apps/data/media-security/vault.py": ("Vault Transit", "/encrypt/", "/decrypt/", "vault:v"),
    "apps/data/report-search/search.py": (
        "tenant_id.keyword", "MAX_DAYS = 92", "source.get(\"tenant_id\") != tenant_id",
        "authoritative", "volumeos-reports-v1", "volumeos-logs-v1",
    ),
    "apps/data/report-search/main.py": ("OIDCAuthenticator", "claims.can(\"reader\")", "ReportSearch"),
    "fluvio/connectors/census-kafka-sink.yaml": (
        "version: 0.3.2", "type: kafka-sink", "topic: census.pings", "enable.idempotence: true",
    ),
}
for relative, required_values in m3_contracts.items():
    path = ROOT / relative
    if not path.is_file():
        continue
    text = path.read_text()
    for required in required_values:
        if required not in text:
            errors.append(f"{relative}: missing M3 contract {required}")

m3_catalog = json.loads((ROOT / "apps/data/lakehouse/catalog.json").read_text())
m3_tables = {row["name"]: row for row in m3_catalog.get("tables", [])}
required_m3_tables = {
    "br_events_usage", "br_events_message", "br_census_pings", "br_scrape_runs", "br_cdc_*",
    "sl_dim_tenant", "sl_dim_lead", "sl_dim_poi", "sl_fact_message", "sl_fact_usage",
    "sl_fact_payment", "sl_fact_ai_job", "gt_campaign_perf", "gt_territory_scores", "gt_lga_density",
    "gt_client_health", "gt_cost_per_client", "gt_revenue_by_offer", "gt_model_registry",
}
if set(m3_tables) != required_m3_tables:
    errors.append("M3 medallion catalog does not exactly cover the reviewed table inventory")
for name, spec in m3_tables.items():
    if not spec.get("owner") or not spec.get("required_columns") or not spec.get("partitions"):
        errors.append(f"M3 catalog table {name} lacks owner, schema, or partitions")

for env in ("dev", "stage", "prod"):
    overlay_text = (ROOT / f"deploy/overlays/{env}/kustomization.yaml").read_text()
    for resource in ("apps/core/svc-census-sync/k8s", "apps/core/svc-query/k8s",
                     "apps/data/lakehouse/k8s", "apps/data/report-search/k8s"):
        if resource not in overlay_text:
            errors.append(f"{env} overlay is missing M3 resource {resource}")
for required in ("/v1/tenants/*/census/*", "/v1/query", "/v1/tenants/*/search/*"):
    if required not in platform_config:
        errors.append(f"M3 gateway is missing {required}")
for required in ("allow-m3-core-data-egress", "allow-report-search-access", "svc-census-sync, svc-query"):
    if required not in network_policy:
        errors.append(f"M3 NetworkPolicy is missing {required}")

# Offer operations source gates: signed front-desk intake, consent-gated redacted
# AI, exact-draft human approval, durable RLS state, and exact-artifact batch review.
offer_required_files = (
    "apps/core/svc-conversations/main.py", "apps/core/svc-conversations/conversations.py",
    "apps/core/svc-conversations/store.py", "apps/core/svc-conversations/providers.py",
    "apps/core/svc-conversations/migrations/001_conversations.sql",
    "apps/core/svc-conversations/tests/test_api.py", "apps/core/svc-conversations/tests/test_conversations.py",
    "apps/workers/offer-worker/worker.py", "apps/workers/offer-worker/front_desk.py",
    "apps/workers/offer-worker/scorecards.py", "apps/workers/offer-worker/books.py",
    "apps/workers/offer-worker/compliance.py", "apps/workers/offer-worker/funding.py",
    "apps/workers/offer-worker/tests/test_offers.py", "apps/workers/offer-worker/tests/test_worker.py",
)
for relative in offer_required_files:
    if not (ROOT / relative).is_file():
        errors.append(f"offer operations: missing {relative}")
conversation_contracts = {
    "apps/core/svc-conversations/conversations.py": (
        "hmac.compare_digest", 'value["metadata"]["phone_number_id"]',
        'subject = f"whatsapp:+{recipient}"', '"service-delivery"', '"ai-processing"',
        "not await self.store.allowed", "draft.digest",
    ),
    "apps/core/svc-conversations/store.py": (
        "class PostgresConversationStore", "set_config('volumeos.tenant_id'",
        "ON CONFLICT", "outbound_for_draft",
    ),
    "apps/core/svc-conversations/providers.py": (
        'frontdesk:{draft.tenant_id}:{draft.digest}', "PrivacyAIClient", "WhatsAppCloudSender",
    ),
    "apps/core/svc-conversations/migrations/001_conversations.sql": (
        "ENABLE ROW LEVEL SECURITY", "FORCE ROW LEVEL SECURITY",
        "reject_conversation_evidence_mutation", "conversation_outbound_draft_unique",
    ),
    "apps/workers/offer-worker/front_desk.py": (
        'result.get("confidence")', "redacted AI response must include numeric confidence",
    ),
    "apps/workers/offer-worker/worker.py": (
        'BATCH_OFFERS = {"03", "05", "07", "08"}', 'task_queue="offers-v1"',
        'required = 2 if job.offer == "08" else 1', "not self.release_complete",
        '@workflow.query(name="status")',
    ),
}
for relative, required_values in conversation_contracts.items():
    path = ROOT / relative
    if not path.is_file():
        continue
    text = path.read_text()
    for required in required_values:
        if required not in text:
            errors.append(f"{relative}: missing offer safety contract {required}")

if errors:
    print("\n".join(f"ERROR {e}" for e in errors), file=sys.stderr)
    sys.exit(1)
print(f"OK: {len(yaml_files)} YAML files, {len(json_files)} JSON files, {len(acceptance_ids)} M1 acceptance checks, {len(list(apps.glob('*.yaml')))} pinned Argo applications, {len(required_topics)} Kafka topics")
