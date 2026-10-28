#!/usr/bin/env python3
"""Fail-closed external operator configuration checks; no infrastructure mutation."""
import argparse
import json
from pathlib import Path
import re
import sys

SECRET_PATTERN = re.compile(r"(?i)(password|token|api[_-]?key|secret)\s*[:=]\s*['\"]?(?!secret/|null|false|true|$)([^\s'\"]{8,})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    path = Path(args.config)
    raw = path.read_text()
    config = json.loads(raw)
    errors = []
    for key in ("domain", "oidc_issuer", "registry", "database_secret", "tigerbeetle_secret", "oidc_client_id"):
        if not config.get(key):
            errors.append(f"missing {key}")
    if config.get("mojaloop_enabled") is not False:
        errors.append("Mojaloop must remain disabled for v1")
    if config.get("production_data_imported") is not False:
        errors.append("production data must not be imported into an unverified operator environment")
    for key in ("database_secret", "tigerbeetle_secret"):
        if config.get(key) and not str(config[key]).startswith("secret/"):
            errors.append(f"{key} must be a secret reference")
    if SECRET_PATTERN.search(raw):
        errors.append("inline credential-like material detected")
    if errors:
        print("\n".join(f"ERROR {error}" for error in errors), file=sys.stderr)
        return 1
    print("OK: external operator source preflight passed; live acceptance still required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
