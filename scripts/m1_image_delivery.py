#!/usr/bin/env python3
"""Fail-closed M1 image build, smoke, publication, and digest verification.

Podman and skopeo use their existing authentication sessions. Credentials are
never accepted as command-line arguments or written to delivery reports.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.request import urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]
REVISION = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IMAGES = (
    {"name": "svc-tenancy", "tag": "0.2.0", "context": "apps/core/svc-tenancy", "user": "65532:65532"},
    {"name": "svc-provisioning", "tag": "0.1.0", "context": "apps/core/svc-provisioning", "user": "65532:65532"},
    {"name": "svc-orchestrator", "tag": "0.3.0", "context": "apps/core/svc-orchestrator", "user": "10001"},
    {"name": "campaign-worker", "tag": "0.2.0", "context": "apps/workers/campaign-worker", "user": "10001"},
    {"name": "studio-worker", "tag": "0.2.0", "context": "apps/workers/studio-worker", "user": "10001"},
    {"name": "synthetic-probe", "tag": "0.1.0", "context": "apps/workers/synthetic-probe", "user": "10001"},
)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def reference(image: dict[str, str]) -> str:
    return f"ghcr.io/munisp/{image['name']}:{image['tag']}"


def save_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def sanitize(value: str) -> str:
    # Delivery commands contain no credentials. Limit diagnostics so an engine
    # error cannot accidentally dump a large environment or build context.
    return value[-2000:]


class Tools:
    def __init__(self, podman: str = "podman", skopeo: str = "skopeo"):
        self.podman = podman
        self.skopeo = skopeo

    def require(self, skopeo: bool = False) -> None:
        if not shutil.which(self.podman):
            raise RuntimeError(f"required executable not found: {self.podman}")
        if skopeo and not shutil.which(self.skopeo):
            raise RuntimeError(f"required executable not found: {self.skopeo}")

    def run(self, args: list[str], timeout: int = 120, skopeo: bool = False) -> subprocess.CompletedProcess[str]:
        executable = self.skopeo if skopeo else self.podman
        try:
            return subprocess.run([executable, *args], capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"{executable} execution failed: {type(exc).__name__}") from exc

    def check(self, args: list[str], timeout: int = 120, skopeo: bool = False) -> str:
        completed = self.run(args, timeout=timeout, skopeo=skopeo)
        if completed.returncode:
            raise RuntimeError(sanitize(completed.stderr or completed.stdout or "container tool failed"))
        return completed.stdout.strip()

    def inspect(self, image: str) -> dict[str, Any]:
        payload = json.loads(self.check(["image", "inspect", image]))
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise RuntimeError(f"invalid image inspection for {image}")
        return payload[0]

    def remote_digest(self, image: str) -> str:
        value = self.check(["inspect", "--format", "{{.Digest}}", f"docker://{image}"], timeout=90, skopeo=True)
        if not DIGEST.fullmatch(value):
            raise RuntimeError(f"registry did not return a valid digest for {image}")
        return value


def git_guard(tools: Tools, revision: str) -> None:
    if not REVISION.fullmatch(revision):
        raise ValueError("source revision must be a full 40-character lowercase Git commit SHA")
    head_result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=False)
    if head_result.returncode or head_result.stdout.strip() != revision or status.returncode or status.stdout.strip():
        raise RuntimeError("HEAD must equal the reviewed source revision and the Git tree must be clean")


def inspect_current(tools: Tools, image: dict[str, str], revision: str | None = None) -> dict[str, str]:
    inspected = tools.inspect(reference(image))
    identity = str(inspected.get("Id", ""))
    config = inspected.get("Config", {})
    user = str(config.get("User", ""))
    if not identity or user != image["user"]:
        raise RuntimeError(f"{image['name']} has an unexpected image ID or runtime user")
    if revision and str(config.get("Labels", {}).get("org.opencontainers.image.revision", "")) != revision:
        raise RuntimeError(f"{image['name']} does not carry the reviewed source-revision label")
    return {"reference": reference(image), "id": identity, "user": user}


def require_report(path: Path, status: str, revision: str) -> dict[str, Any]:
    report = load_json(path)
    if report.get("schema_version") != 1 or report.get("status") != status or report.get("source_revision") != revision:
        raise RuntimeError(f"{path.name} is not a current {status} report for the reviewed revision")
    return report


def verify_unchanged(tools: Tools, report: dict[str, Any]) -> None:
    entries = report.get("images")
    if not isinstance(entries, dict) or set(entries) != {item["name"] for item in IMAGES}:
        raise RuntimeError(f"delivery report does not contain exactly the {len(IMAGES)} M1 images")
    for image in IMAGES:
        current = inspect_current(tools, image, str(report["source_revision"]))
        recorded = entries[image["name"]]
        if current["id"] != recorded.get("id") or current["user"] != recorded.get("user"):
            raise RuntimeError(f"{image['name']} changed after its delivery evidence was recorded")


def build(args: argparse.Namespace) -> int:
    tools = Tools(os.environ.get("PODMAN", "podman"))
    tools.require()
    git_guard(tools, args.source_revision)
    output = Path(args.output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("delivery output must be a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "schema_version": 1, "status": "building", "source_revision": args.source_revision,
        "created_at": now(), "images": {},
    }
    save_json(output / "build.json", report)
    try:
        for image in IMAGES:
            context = ROOT / image["context"]
            tools.check([
                "build", "--pull=always", "--no-cache", "--format", "docker",
                "--label", f"org.opencontainers.image.revision={args.source_revision}",
                "--label", "org.opencontainers.image.source=https://github.com/munisp/volume0S",
                "--file", str(context / "Dockerfile"), "--tag", reference(image), str(context),
            ], timeout=1800)
            report["images"][image["name"]] = inspect_current(tools, image, args.source_revision)
            save_json(output / "build.json", report)
        report["status"] = "built"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = sanitize(str(exc))
        save_json(output / "build.json", report)
        raise
    report["completed_at"] = now()
    save_json(output / "build.json", report)
    print(f"Built {len(IMAGES)} M1 images for {args.source_revision}")
    return 0


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def http_body(port: int, path: str, expected: str) -> None:
    deadline = time.monotonic() + 20
    last_error = "endpoint did not become ready"
    while time.monotonic() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
                body = response.read().decode("utf-8")
                if response.status == 200 and expected in body:
                    return
                last_error = f"unexpected HTTP response from {path}"
        except Exception as exc:
            last_error = type(exc).__name__
        time.sleep(0.25)
    raise RuntimeError(f"smoke endpoint failed for {path}: {last_error}")


def smoke_service(tools: Tools, image: dict[str, str], checks: list[dict[str, str]]) -> None:
    name = "volumeos-smoke-" + uuid.uuid4().hex[:12]
    port = free_port()
    is_core = image["name"] in {"svc-tenancy", "svc-orchestrator"}
    command = [] if is_core else [
        "python", "-c", "import metrics,time; metrics.start_server(9091); time.sleep(120)",
    ]
    run_args = [
        "run", "--detach", "--rm", "--name", name, "--user", image["user"],
        "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=128",
        "--publish", f"127.0.0.1:{port}:8080" if is_core else f"127.0.0.1:{port}:9091",
    ]
    if image["name"] == "svc-tenancy":
        run_args += ["--env", "STORE=memory", "--env", "VO_ENV=dev", "--env", "PORT=8080"]
    if image["name"] == "svc-orchestrator":
        # Synthetic startup-only values exercise the real ASGI health endpoint;
        # no identity, Temporal, or object-storage call is treated as accepted.
        run_args += [
            "--env", "OIDC_ISSUER=https://identity.invalid/realms/volumeos",
            "--env", "OIDC_AUDIENCE=core-services", "--env", "OIDC_JWKS_URL=https://identity.invalid/jwks",
            "--env", "ASSET_BUCKET=assets", "--env", "AWS_ACCESS_KEY_ID=smoke",
            "--env", "AWS_SECRET_ACCESS_KEY=smoke", "--env", "TEMPORAL_HOSTPORT=127.0.0.1:7233",
        ]
    try:
        tools.check([*run_args, reference(image), *command], timeout=30)
        if is_core:
            http_body(port, "/healthz", '"status":"ok"')
            if image["name"] == "svc-tenancy":
                http_body(port, "/metrics", "volumeos_http_requests_total")
        else:
            metric = "volumeos_campaign_approvals_total" if image["name"] == "campaign-worker" else "volumeos_studio_packs_total"
            http_body(port, "/metrics", metric)
        checks.append({"id": f"runtime-{image['name']}", "status": "passed"})
    finally:
        tools.run(["stop", "--time", "1", name], timeout=15)


def expect_startup_failure(tools: Tools, image: dict[str, str], checks: list[dict[str, str]]) -> None:
    completed = tools.run([
        "run", "--rm", "--user", image["user"], "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--network=none", reference(image),
    ], timeout=45)
    if completed.returncode == 0:
        raise RuntimeError(f"{image['name']} unexpectedly started without required dependencies/configuration")
    checks.append({"id": f"fail-closed-{image['name']}", "status": "passed"})


def smoke(args: argparse.Namespace) -> int:
    tools = Tools(os.environ.get("PODMAN", "podman"))
    tools.require()
    output = Path(args.output)
    build_report = require_report(output / "build.json", "built", args.source_revision)
    git_guard(tools, args.source_revision)
    verify_unchanged(tools, build_report)
    report: dict[str, Any] = {
        "schema_version": 1, "status": "running", "source_revision": args.source_revision,
        "created_at": now(), "checks": [],
    }
    save_json(output / "smoke.json", report)
    try:
        for image in IMAGES:
            if image["name"] in {"svc-tenancy", "svc-orchestrator", "campaign-worker", "studio-worker"}:
                smoke_service(tools, image, report["checks"])
            else:
                expect_startup_failure(tools, image, report["checks"])
            save_json(output / "smoke.json", report)
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = sanitize(str(exc))
        save_json(output / "smoke.json", report)
        raise
    report["completed_at"] = now()
    save_json(output / "smoke.json", report)
    print(f"Passed {len(report['checks'])} production-mode image smoke checks")
    return 0


def publish(args: argparse.Namespace) -> int:
    tools = Tools(os.environ.get("PODMAN", "podman"), os.environ.get("SKOPEO", "skopeo"))
    tools.require(skopeo=True)
    output = Path(args.output)
    build_report = require_report(output / "build.json", "built", args.source_revision)
    require_report(output / "smoke.json", "passed", args.source_revision)
    git_guard(tools, args.source_revision)
    verify_unchanged(tools, build_report)
    report: dict[str, Any] = {
        "schema_version": 1, "status": "publishing", "source_revision": args.source_revision,
        "created_at": now(), "images": {},
    }
    save_json(output / "images.json", report)
    try:
        for image in IMAGES:
            ref = reference(image)
            tools.check(["push", ref], timeout=900)
            digest = tools.remote_digest(ref)
            report["images"][image["name"]] = {"reference": ref, "digest": digest, "pinned": f"{ref}@{digest}"}
            save_json(output / "images.json", report)
        report["status"] = "published"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = sanitize(str(exc))
        save_json(output / "images.json", report)
        raise
    report["completed_at"] = now()
    save_json(output / "images.json", report)
    print(f"Published and verified {len(IMAGES)} immutable registry digests")
    return 0


def verify(args: argparse.Namespace) -> int:
    tools = Tools(os.environ.get("PODMAN", "podman"), os.environ.get("SKOPEO", "skopeo"))
    tools.require(skopeo=True)
    output = Path(args.output)
    report = require_report(output / "images.json", "published", args.source_revision)
    entries = report.get("images")
    if not isinstance(entries, dict) or set(entries) != {image["name"] for image in IMAGES}:
        raise RuntimeError("publication report does not contain exactly the M1 images")
    for image in IMAGES:
        entry = entries[image["name"]]
        ref = reference(image)
        if entry.get("reference") != ref or not DIGEST.fullmatch(str(entry.get("digest", ""))):
            raise RuntimeError(f"invalid publication record for {image['name']}")
        if entry.get("pinned") != f"{ref}@{entry['digest']}":
            raise RuntimeError(f"invalid immutable reference for {image['name']}")
        if tools.remote_digest(ref) != entry["digest"]:
            raise RuntimeError(f"registry digest changed for {image['name']}")
    verification = {
        "schema_version": 1, "status": "passed", "source_revision": args.source_revision,
        "verified_at": now(), "images": entries,
    }
    save_json(output / "verification.json", verification)
    print(f"Verified all {len(IMAGES)} registry digests against the publication report")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for name, function in (("build", build), ("smoke", smoke), ("publish", publish), ("verify", verify)):
        command = commands.add_parser(name)
        command.add_argument("--source-revision", required=True)
        command.add_argument("--output", required=True)
        command.set_defaults(function=function)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        return int(args.function(args))
    except Exception as exc:
        print(f"ERROR {sanitize(str(exc))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
