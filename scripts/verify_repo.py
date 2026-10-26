#!/usr/bin/env python3
"""Repository-level M0/M1 checks; no Kubernetes cluster credentials required."""
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
