#!/usr/bin/env python3
"""Fail-closed M1 staging preflight and checksummed evidence capture.

This utility never supplies provider credentials, changes cluster state, or marks
an operational check passed. Passing an acceptance check requires an operator to
attach real evidence with the record command.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "docs/m1/acceptance/checks.json"
STATUSES = {"passed", "failed", "blocked"}
SECRET_WORDS = re.compile(
    r"(?i)(authorization:\s*bearer\s+|-----begin (?:rsa |ec )?private key-----|"
    r"(?:password|api[_-]?key|client[_-]?secret|access[_-]?token)\s*[:=]\s*)\S+"
)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def save_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def sanitize(value: str) -> str:
    value = SECRET_WORDS.sub(lambda match: match.group(1) + "[REDACTED]", value)
    return value[-2000:]


def catalog() -> dict[str, dict[str, Any]]:
    checks = load_json(CATALOG)["checks"]
    result = {check["id"]: check for check in checks}
    if len(result) != len(checks):
        raise ValueError("acceptance catalog contains duplicate IDs")
    return result


def initial_run(args: argparse.Namespace) -> dict[str, Any]:
    checks = catalog()
    return {
        "schema_version": 1,
        "milestone": "M1",
        "status": "incomplete",
        "environment": args.environment,
        "context": args.context,
        "source_revision": args.source_revision,
        "created_at": now(),
        "updated_at": now(),
        "checks": {
            check_id: {"status": "blocked", "notes": "No acceptance evidence recorded", "artifacts": []}
            for check_id in checks
        },
    }


def validate_identity(args: argparse.Namespace) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,30}", args.environment):
        raise ValueError("environment must be an explicit lowercase environment name")
    if not args.context.strip():
        raise ValueError("an explicit Kubernetes context is required")
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_revision):
        raise ValueError("source revision must be a full 40-character lowercase Git commit SHA")


def prepare_output(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise ValueError("evidence output must be a new or empty directory; use a new directory for each run")
    path.mkdir(parents=True, exist_ok=True)
    (path / "artifacts").mkdir(exist_ok=True)


def result(check_id: str, status: str, detail: str) -> dict[str, str]:
    if status not in STATUSES:
        raise ValueError(status)
    return {"id": check_id, "status": status, "detail": sanitize(detail), "checked_at": now()}


class Cluster:
    def __init__(self, executable: str | None, context: str):
        self.executable = executable
        self.context = context
        self.available = bool(executable)

    def get(self, args: list[str]) -> tuple[int, dict[str, Any] | str]:
        if not self.executable:
            return 127, "kubectl was not found"
        command = [self.executable, "--context", self.context, *args, "-o", "json"]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 124, sanitize(str(exc))
        if completed.returncode:
            return completed.returncode, sanitize(completed.stderr or completed.stdout)
        try:
            return 0, json.loads(completed.stdout)
        except json.JSONDecodeError:
            return 125, "kubectl returned invalid JSON"


def provider_config(path: Path, environment: str) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        config = load_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, [f"provider configuration is unreadable: {exc}"]
    errors: list[str] = []
    if config.get("schema_version") != 1 or config.get("environment") != environment:
        errors.append("provider configuration schema/environment does not match the run")
    providers = config.get("providers")
    if not isinstance(providers, dict):
        errors.append("providers must be an object")
        providers = {}
    for channel in ("email", "sms", "whatsapp", "social"):
        provider = providers.get(channel)
        if not isinstance(provider, dict):
            errors.append(f"{channel} provider is missing")
            continue
        name = str(provider.get("name", ""))
        if provider.get("configured") is not True or provider.get("mode") not in {"sandbox", "production"}:
            errors.append(f"{channel} provider is not explicitly configured")
        if not name or "REPLACE_" in name or name.lower() in {"example", "placeholder", "todo"}:
            errors.append(f"{channel} provider name is a placeholder")
    comfyui = config.get("comfyui")
    if not isinstance(comfyui, dict) or not comfyui.get("deployment") or not comfyui.get("namespace"):
        errors.append("ComfyUI namespace and deployment are required")
    return config, errors


def names(payload: dict[str, Any] | str) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    return {str(item.get("metadata", {}).get("name", "")) for item in payload.get("items", [])}


def preflight(args: argparse.Namespace) -> int:
    validate_identity(args)
    output = Path(args.output).resolve()
    prepare_output(output)
    run = initial_run(args)
    save_json(output / "run.json", run)
    executable = shutil.which(os.environ.get("KUBECTL", "kubectl"))
    cluster = Cluster(executable, args.context)
    config, config_errors = provider_config(Path(args.provider_config), args.environment)
    results: list[dict[str, str]] = []
    results.append(result("PF-PROVIDERS", "failed" if config_errors else "passed",
                          "; ".join(config_errors) or "Secret-free provider configuration validated"))
    if not cluster.available:
        results.extend(result(check_id, "blocked", "kubectl is unavailable; no cluster check was attempted")
                       for check_id in ("PF-CONTEXT", "PF-WORKLOADS", "PF-SECRETS", "PF-TENANT-STORE", "PF-GPU", "PF-OBSERVABILITY"))
    else:
        code, payload = cluster.get(["get", "namespace", "core"])
        results.append(result("PF-CONTEXT", "passed" if code == 0 else "blocked",
                              "Expected context reached the core namespace" if code == 0 else f"Cluster access failed: {payload}"))
        expected = {
            "core": {"svc-tenancy", "svc-provisioning", "svc-orchestrator", "listmonk", "postiz"},
            "workers": {"campaign-worker", "studio-worker", "synthetic-probe"},
            "ai": {str((config or {}).get("comfyui", {}).get("deployment", "comfyui"))},
        }
        missing: list[str] = []
        inaccessible: list[str] = []
        for namespace, required in expected.items():
            code, payload = cluster.get(["-n", namespace, "get", "deployments"])
            if code:
                inaccessible.append(f"{namespace}: {payload}")
                continue
            found = names(payload)
            missing.extend(f"{namespace}/{name}" for name in sorted(required - found))
            for item in payload.get("items", []) if isinstance(payload, dict) else []:
                name = str(item.get("metadata", {}).get("name", ""))
                if name not in required:
                    continue
                spec = item.get("spec", {})
                status = item.get("status", {})
                desired = int(spec.get("replicas", 1))
                if desired < 1 or int(status.get("readyReplicas", 0)) != desired or int(status.get("updatedReplicas", 0)) != desired:
                    missing.append(f"{namespace}/{name} is not fully ready")
        status = "blocked" if inaccessible else ("failed" if missing else "passed")
        if inaccessible:
            detail = "; ".join(inaccessible)
        elif missing:
            detail = f"Missing deployments: {', '.join(missing)}"
        else:
            detail = "Required M1 deployments exist"
        results.append(result("PF-WORKLOADS", status, detail))

        required_secrets = {
            ("core", "svc-tenancy"): {"database-url", "service-token", "suppression-hmac-secret"},
            ("core", "svc-provisioning"): {"service-token", "keycloak-admin-client-id", "keycloak-admin-client-secret"},
            ("core", "svc-orchestrator"): {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"},
            ("core", "listmonk-admin"): {"admin-password"},
            ("core", "postiz"): {"DATABASE_URL", "REDIS_URL", "JWT_SECRET"},
            ("workers", "studio-worker"): {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"},
            ("workers", "synthetic-probe"): {"client-id", "client-secret", "service-token"},
        }
        secret_errors: list[str] = []
        for (namespace, name), keys in required_secrets.items():
            code, payload = cluster.get(["-n", namespace, "get", "secret", name])
            if code:
                secret_errors.append(f"{namespace}/{name} unavailable")
            elif not isinstance(payload, dict) or any(not payload.get("data", {}).get(key) for key in keys):
                secret_errors.append(f"{namespace}/{name} lacks required nonempty keys")
        results.append(result("PF-SECRETS", "failed" if secret_errors else "passed",
                              "; ".join(secret_errors) or "Required secret names and keys exist; values were not recorded"))

        code, payload = cluster.get(["-n", "core", "get", "components.dapr.io", "volumeos-tenant-secrets"])
        results.append(result("PF-TENANT-STORE", "passed" if code == 0 else "failed",
                              "Private Dapr tenant secret store exists" if code == 0 else f"Tenant secret store unavailable: {payload}"))

        code, payload = cluster.get(["get", "nodes"])
        gpu_nodes: list[str] = []
        if code == 0 and isinstance(payload, dict):
            for node in payload.get("items", []):
                capacity = node.get("status", {}).get("capacity", {})
                try:
                    has_gpu = int(str(capacity.get("nvidia.com/gpu", "0"))) > 0
                except ValueError:
                    has_gpu = False
                if has_gpu:
                    gpu_nodes.append(str(node.get("metadata", {}).get("name", "unknown")))
        results.append(result("PF-GPU", "passed" if gpu_nodes else "failed",
                              f"GPU capacity on {', '.join(gpu_nodes)}" if gpu_nodes else "No node reports nvidia.com/gpu capacity"))

        observation_errors: list[str] = []
        required_observation = (
            ("prometheusrule", "volumeos-m1-slo"),
            ("configmap", "volumeos-m1-grafana-dashboard"),
            ("servicemonitor", "svc-tenancy"),
            ("servicemonitor", "campaign-worker"),
            ("servicemonitor", "studio-worker"),
            ("servicemonitor", "synthetic-probe"),
        )
        for kind, name in required_observation:
            code, payload = cluster.get(["-n", "ops", "get", kind, name])
            if code:
                observation_errors.append(f"ops/{name} unavailable")
        results.append(result("PF-OBSERVABILITY", "failed" if observation_errors else "passed",
                              "; ".join(observation_errors) or "M1 rules, dashboard, and service/probe scrape resources exist"))
    overall = "passed" if all(item["status"] == "passed" for item in results) else "blocked"
    report = {
        "schema_version": 1,
        "status": overall,
        "environment": args.environment,
        "context": args.context,
        "source_revision": args.source_revision,
        "created_at": now(),
        "checks": results,
    }
    save_json(output / "preflight.json", report)
    print(json.dumps({"output": str(output), "status": overall, "checks": results}, indent=2))
    return 0 if overall == "passed" else 1


def load_run(path: Path) -> dict[str, Any]:
    run = load_json(path)
    expected = set(catalog())
    if run.get("schema_version") != 1 or set(run.get("checks", {})) != expected:
        raise ValueError("run.json does not match the current M1 acceptance catalog")
    return run


def record(args: argparse.Namespace) -> int:
    output = Path(args.output).resolve()
    run_path = output / "run.json"
    run = load_run(run_path)
    if args.status not in STATUSES:
        raise ValueError("invalid status")
    check_id = args.check
    if args.status == "passed" and not args.evidence:
        raise ValueError("a passed check requires at least one evidence file")
    if args.status != "passed" and not args.notes.strip():
        raise ValueError("failed or blocked checks require notes")
    artifacts: list[dict[str, str]] = []
    for source_name in args.evidence:
        source_input = Path(source_name)
        if source_input.is_symlink():
            raise ValueError(f"evidence must not be a symbolic link: {source_input}")
        source = source_input.resolve()
        if not source.is_file():
            raise ValueError(f"evidence must be a regular file: {source}")
        destination = output / "artifacts" / f"{check_id}-{source.name}"
        if destination.exists():
            raise ValueError(f"artifact already exists: {destination.name}")
        shutil.copyfile(source, destination)
        artifacts.append({"path": str(destination.relative_to(output)), "sha256": digest(destination)})
    previous = run["checks"][check_id]
    history = list(previous.get("history", []))
    if previous.get("recorded_at"):
        history.append(previous)
    run["checks"][check_id] = {
        "status": args.status,
        "notes": sanitize(args.notes),
        "recorded_at": now(),
        "artifacts": artifacts,
        "history": history,
    }
    run["status"] = "incomplete"
    run["updated_at"] = now()
    save_json(run_path, run)
    print(f"{check_id}: {args.status}; acceptance remains incomplete until verify passes")
    return 0


def verify(args: argparse.Namespace) -> int:
    output = Path(args.output).resolve()
    run = load_run(output / "run.json")
    errors: list[str] = []
    preflight_path = output / "preflight.json"
    try:
        preflight = load_json(preflight_path)
        if preflight.get("status") != "passed":
            errors.append("preflight has not passed")
        for key in ("environment", "context", "source_revision"):
            if preflight.get(key) != run.get(key):
                errors.append(f"preflight {key} does not match run identity")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"preflight is unavailable: {exc}")
    for check_id, check in run["checks"].items():
        if check.get("status") != "passed":
            errors.append(f"{check_id}: {check.get('status', 'unknown')}")
        artifacts = check.get("artifacts", [])
        if check.get("status") == "passed" and not artifacts:
            errors.append(f"{check_id}: passed without evidence")
        for artifact in artifacts:
            path = output / artifact.get("path", "")
            try:
                if not path.is_file() or digest(path) != artifact.get("sha256"):
                    errors.append(f"{check_id}: missing or changed artifact {path.name}")
            except OSError as exc:
                errors.append(f"{check_id}: unreadable artifact {path.name}: {exc}")
    run["status"] = "accepted" if not errors else "incomplete"
    run["updated_at"] = now()
    save_json(output / "run.json", run)
    report = {"status": run["status"], "errors": errors, "evidence_directory": str(output)}
    save_json(output / "verification.json", report)
    print(json.dumps(report, indent=2))
    return 0 if not errors else 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    pre = commands.add_parser("preflight", help="create a run and execute read-only cluster preflight checks")
    pre.add_argument("--environment", required=True)
    pre.add_argument("--context", required=True)
    pre.add_argument("--source-revision", required=True)
    pre.add_argument("--provider-config", required=True)
    pre.add_argument("--output", required=True)
    pre.set_defaults(func=preflight)
    rec = commands.add_parser("record", help="record one manual acceptance check with evidence")
    rec.add_argument("--output", required=True)
    rec.add_argument("--check", required=True, choices=sorted(catalog()))
    rec.add_argument("--status", required=True, choices=sorted(STATUSES))
    rec.add_argument("--notes", default="")
    rec.add_argument("--evidence", action="append", default=[])
    rec.set_defaults(func=record)
    ver = commands.add_parser("verify", help="fail unless preflight and all evidence checks pass")
    ver.add_argument("--output", required=True)
    ver.set_defaults(func=verify)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return int(args.func(args))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {sanitize(str(exc))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
