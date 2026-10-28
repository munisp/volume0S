import copy

from restore_drill import REQUIRED_CHECKS, preflight, signature, verify

KEY = b"r" * 32


def manifest():
    return {
        "environment": "isolated", "production_credentials": False, "provider_egress": False,
        "backup_id": "backup-1", "restore_cluster": "restore-1", "started_at": "2026-01-01T00:00:00Z",
        "completed_at": "2026-01-01T01:00:00Z", "operator": "operator-1",
        "deletion_tombstone_log": "objects/deletion-log.json", "rpo_seconds": 10, "rpo_seconds_target": 60,
        "rto_seconds": 100, "rto_seconds_target": 300, "reviewers": ["reviewer-1", "reviewer-2"],
        "checks": {name: {"passed": True, "evidence_sha256": "a" * 64} for name in REQUIRED_CHECKS},
    }


def test_valid_signed_restore_evidence():
    document = manifest()
    document["evidence_hmac"] = signature(document, KEY)
    assert verify(document, KEY) == []


def test_backup_existence_is_not_restore_evidence():
    document = manifest()
    document["checks"]["tenant_rls_probes"] = {"passed": False, "evidence_sha256": "a" * 64}
    document["evidence_hmac"] = signature(document, KEY)
    assert any("tenant_rls_probes" in error for error in verify(document, KEY))


def test_tampered_or_unsigned_manifest_fails():
    document = manifest()
    assert any("HMAC" in error for error in verify(document, KEY))
    document["evidence_hmac"] = signature(document, KEY)
    document["rto_seconds"] = 9999
    assert verify(document, KEY)


def test_production_egress_fails_preflight():
    document = manifest()
    document["provider_egress"] = True
    assert preflight(document)
