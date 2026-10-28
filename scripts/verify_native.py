#!/usr/bin/env python3
"""Cross-platform native source and API-contract invariants; not a device build."""
from pathlib import Path
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "apps/native/field-app"


def verify(root: Path = ROOT) -> list[str]:
    native = root / "apps/native/field-app"
    errors = []
    required = [
        "android/app/src/main/java/os/volume/field/auth/OidcAuth.kt",
        "android/app/src/main/java/os/volume/field/census/CensusRecord.kt",
        "android/app/src/main/java/os/volume/field/outbox/EncryptedOutbox.kt",
        "android/app/src/main/java/os/volume/field/sync/ChunkSync.kt",
        "android/app/src/test/java/os/volume/field/census/CensusRecordTest.kt",
        "ios/FieldApp/Auth/OIDCAuth.swift", "ios/FieldApp/Census/CensusRecord.swift",
        "ios/FieldApp/Outbox/EncryptedOutbox.swift", "ios/FieldApp/Sync/ChunkSync.swift",
        "ios/FieldAppTests/FieldAppTests.swift", "ios/project.yml", "shared/test-vectors.json",
        "shared/census-record.schema.json", "shared/sync-contract.json", "README.md",
    ]
    for name in required:
        if not (native / name).is_file():
            errors.append(f"missing {name}")
    if errors:
        return errors
    android = "\n".join(path.read_text() for path in (native / "android/app/src/main").rglob("*.kt"))
    ios = "\n".join(path.read_text() for path in (native / "ios/FieldApp").rglob("*.swift"))
    for source, name, markers in (
        (android, "Android", ["ResponseTypeValues.CODE", "setCodeVerifier", "EncryptedSharedPreferences", "SQLiteDatabase", "X-VolumeOS-Device", "expected_sha256", "resume", "published"]),
        (ios, "iOS", ["ASWebAuthenticationSession", "code_challenge", "S256", "Keychain", "sqlite3_key", "X-VolumeOS-Device", "expected_sha256", "resume", "published"]),
    ):
        for marker in markers:
            if marker not in source:
                errors.append(f"{name} lacks {marker}")
        if re.search(r"(?i)client_secret\s*[=:]\s*[\"'][^\"']+", source):
            errors.append(f"{name} contains an OAuth client secret")
    if "example.invalid" not in android or "example.invalid" not in ios:
        errors.append("native examples must use non-routable endpoints")
    for name in ("shared/census-record.schema.json", "shared/sync-contract.json", "shared/test-vectors.json"):
        try:
            json.loads((native / name).read_text())
        except json.JSONDecodeError as exc:
            errors.append(f"invalid {name}: {exc}")
    return errors


def main() -> int:
    errors = verify()
    if errors:
        print("\n".join(f"ERROR native: {error}" for error in errors), file=sys.stderr)
        return 1
    print("OK: Android/iOS PKCE, secure storage, encrypted bounded outbox, and resumable census contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
