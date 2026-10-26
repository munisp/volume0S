#!/usr/bin/env python3
"""Repository-level M0 checks; no Kubernetes cluster credentials required."""
from pathlib import Path
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

for env in ("dev", "stage", "prod"):
    path = ROOT / f"deploy/overlays/{env}/kustomization.yaml"
    doc = yaml.safe_load(path.read_text())
    for resource in doc.get("resources", []):
        if not (path.parent / resource).exists():
            errors.append(f"{path.relative_to(ROOT)}: missing resource {resource}")

required_topics = {"usage.events", "message.status", "census.pings", "lead.events", "money.events", "ai.jobs", "audit.publish"}
text = (ROOT / "bootstrap/platform/kafka-topics.yaml").read_text()
missing = sorted(topic for topic in required_topics if f"name: {topic}" not in text)
if missing:
    errors.append(f"missing Kafka topics: {', '.join(missing)}")

for path in ROOT.rglob("Dockerfile"):
    text = path.read_text()
    if re.search(r"FROM\s+\S+:latest", text):
        errors.append(f"{path.relative_to(ROOT)}: floating base image")

if errors:
    print("\n".join(f"ERROR {e}" for e in errors), file=sys.stderr)
    sys.exit(1)
print(f"OK: {len(yaml_files)} YAML files, {len(list(apps.glob('*.yaml')))} pinned Argo applications, {len(required_topics)} Kafka topics")
