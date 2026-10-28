#!/usr/bin/env python3
"""Evidence-based quarterly restore drill; never reports success from backup metadata."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
from typing import Any

REQUIRED_CHECKS = {
    "postgres_integrity", "tigerbeetle_integrity", "object_digests", "tenant_rls_probes",
    "temporal_workflow_recovery", "deletion_tombstone_replay", "provider_egress_isolated",
}
SAFE_HOST_LABELS = {"isolated", "restore", "drill", "nonproduction"}


def canonical(document: dict[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


def signature(document: dict[str, Any], key: bytes) -> str:
    body = {k: v for k, v in document.items() if k != "evidence_hmac"}
    return hmac.new(key, canonical(body), hashlib.sha256).hexdigest()


def preflight(config: dict[str, Any]) -> list[str]:
    errors = []
    environment = str(config.get("environment", "")).casefold()
    if environment not in SAFE_HOST_LABELS:
        errors.append("environment must be isolated/restore/drill/nonproduction")
    if config.get("production_credentials") is not False or config.get("provider_egress") is not False:
        errors.append("production credentials and provider egress must be disabled")
    for key in ("backup_id", "restore_cluster", "started_at", "operator"):
        if not config.get(key):
            errors.append(f"missing {key}")
    if not config.get("deletion_tombstone_log"):
        errors.append("deletion tombstone log is required before reopening traffic")
    return errors


def verify(manifest: dict[str, Any], key: bytes) -> list[str]:
    errors = preflight(manifest)
    checks = manifest.get("checks")
    if not isinstance(checks, dict):
        errors.append("checks object is required")
        checks = {}
    for name in sorted(REQUIRED_CHECKS):
        evidence = checks.get(name)
        if not isinstance(evidence, dict) or evidence.get("passed") is not True or not evidence.get("evidence_sha256"):
            errors.append(f"missing passed evidence for {name}")
        elif not isinstance(evidence["evidence_sha256"], str) or len(evidence["evidence_sha256"]) != 64:
            errors.append(f"invalid evidence digest for {name}")
    for name in ("rpo_seconds", "rto_seconds"):
        value = manifest.get(name)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            errors.append(f"{name} must be a non-negative measurement")
        limit = manifest.get(f"{name}_target")
        if not isinstance(limit, (int, float)) or isinstance(limit, bool) or (isinstance(value, (int, float)) and value > limit):
            errors.append(f"{name} exceeds or lacks a valid target")
    reviewers = manifest.get("reviewers")
    if not isinstance(reviewers, list) or len(set(reviewers)) < 2:
        errors.append("two distinct named reviewers are required")
    if not manifest.get("completed_at") or manifest.get("completed_at") == manifest.get("started_at"):
        errors.append("distinct completion timestamp is required")
    supplied = manifest.get("evidence_hmac", "")
    if not isinstance(supplied, str) or not hmac.compare_digest(supplied, signature(manifest, key)):
        errors.append("evidence HMAC is missing or invalid")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "verify"):
        command = sub.add_parser(name)
        command.add_argument("--config" if name == "preflight" else "--manifest", required=True, dest="path")
    args = parser.parse_args()
    document = json.loads(Path(args.path).read_text())
    if args.command == "preflight":
        errors = preflight(document)
    else:
        key = os.environ.get("RESTORE_EVIDENCE_KEY", "").encode()
        if len(key) < 32:
            errors = ["RESTORE_EVIDENCE_KEY must contain at least 32 bytes"]
        else:
            errors = verify(document, key)
    if errors:
        print("\n".join(f"ERROR {error}" for error in errors), file=sys.stderr)
        return 1
    print(f"OK: restore drill {args.command} evidence validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
