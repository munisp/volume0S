#!/usr/bin/env python3
"""M4 source invariants for privacy, security operations, and open-core delivery."""
from pathlib import Path
import json
import re
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def verify(root: Path = ROOT) -> list[str]:
    errors = []
    required = [
        "apps/core/svc-privacy/main.py", "apps/core/svc-privacy/privacy.py",
        "apps/core/svc-privacy/redaction.py", "apps/core/svc-privacy/store.py",
        "apps/core/svc-privacy/migrations/001_privacy.sql", "apps/core/svc-privacy/k8s/deployment.yaml",
        "security/secops/detections.yaml", "security/secops/wazuh-local-rules.xml",
        "security/secops/opensearch-monitor.json", "security/secops/opencti-connector.yaml",
        "security/secops/slo.yaml", "scripts/restore_drill.py", "docs/m4/SECURITY_OPERATIONS.md",
        "docs/m4/OPEN_CORE.md", "deploy/examples/operator-values.yaml", "LICENSE",
    ]
    for name in required:
        if not (root / name).is_file():
            errors.append(f"missing {name}")
    if errors:
        return errors
    migration = (root / "apps/core/svc-privacy/migrations/001_privacy.sql").read_text()
    for table in ("consents", "consent_history", "dsar_requests", "privacy_audit"):
        if f"'{table}'" not in migration or "ENABLE ROW LEVEL SECURITY" not in migration or "FORCE ROW LEVEL SECURITY" not in migration:
            errors.append(f"{table} lacks RLS")
    if migration.count("reject_privacy_mutation") < 3:
        errors.append("append-only privacy triggers missing")
    redaction = (root / "apps/core/svc-privacy/redaction.py").read_text()
    if 'endpoint.startswith("https://")' not in redaction or "self.redactor.redact(prompt)" not in redaction:
        errors.append("AI provider boundary is not fail-closed redaction")
    detections = (root / "security/secops/detections.yaml").read_text()
    for technique in ("T1078", "T1530", "T1565", "T0854", "T1485", "T1567"):
        if technique not in detections:
            errors.append(f"missing MITRE technique {technique}")
    try:
        ET.parse(root / "security/secops/wazuh-local-rules.xml")
        monitor = json.loads((root / "security/secops/opensearch-monitor.json").read_text())
        if not monitor.get("enabled"):
            errors.append("OpenSearch monitor disabled")
    except (ET.ParseError, json.JSONDecodeError) as exc:
        errors.append(f"invalid security configuration: {exc}")
    examples = (root / "deploy/examples/operator-values.yaml").read_text()
    if re.search(r"(?i)(token|password|api_key):\s+(?!null)(?!secret/)[^\s]+", examples):
        errors.append("operator example contains an inline credential")
    return errors


def main() -> int:
    errors = verify()
    if errors:
        print("\n".join(f"ERROR M4: {error}" for error in errors), file=sys.stderr)
        return 1
    print("OK: M4 privacy/RLS, redaction, MITRE detections, restore evidence, and open-core boundaries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
