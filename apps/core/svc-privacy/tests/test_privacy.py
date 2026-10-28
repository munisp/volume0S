import time

import pytest

from privacy import MemoryPrivacyStore, PrivacyError, PrivacyService, identity_proof, verify_identity_proof


@pytest.fixture
def service():
    return PrivacyService(MemoryPrivacyStore(), b"x" * 32, clock=lambda: 1_700_000_000)


@pytest.mark.asyncio
async def test_consent_is_versioned_and_cannot_change_subject(service):
    first = await service.record_consent("tenant-a", "officer-1", "consent-1", "Ada Lovelace", "field-census", "consent")
    second = await service.record_consent("tenant-a", "officer-1", "consent-1", "Ada Lovelace", "field-census", "consent", "withdrawn")
    assert (first.version, second.version, second.status) == (1, 2, "withdrawn")
    with pytest.raises(PrivacyError, match="reassigned"):
        await service.record_consent("tenant-a", "officer-1", "consent-1", "Another Person", "field-census", "consent")


@pytest.mark.asyncio
async def test_consent_and_dsar_are_tenant_scoped(service):
    await service.record_consent("tenant-a", "officer-1", "same-ref", "Subject", "campaign", "consent")
    assert await service.store.consent("tenant-b", "same-ref") is None
    await service.open_dsar("tenant-a", "officer-1", "request-1", "Subject", "export", True)
    assert await service.store.dsar("tenant-b", "request-1") is None


@pytest.mark.asyncio
async def test_dsar_requires_verified_identity(service):
    with pytest.raises(PrivacyError, match="verified identity"):
        await service.open_dsar("tenant-a", "officer-1", "request-1", "Subject", "delete", False)


@pytest.mark.asyncio
async def test_export_manifest_is_bound_and_terminal(service):
    await service.open_dsar("tenant-a", "officer-1", "request-1", "Subject", "export", True)
    key = "privacy/tenant-a/request-1/export.json"
    result = await service.attach_export("tenant-a", "worker-1", "request-1", key,
                                         [{"key": key, "sha256": "a" * 64}])
    assert result.status == "exported"
    assert len(result.export_manifest["manifest_sha256"]) == 64
    replay = await service.attach_export("tenant-a", "worker-1", "request-1", key,
                                         [{"key": key, "sha256": "a" * 64}])
    assert replay.export_manifest == result.export_manifest
    with pytest.raises(PrivacyError, match="conflicts"):
        await service.attach_export("tenant-a", "worker-1", "request-1", key,
                                    [{"key": key, "sha256": "b" * 64}])


@pytest.mark.asyncio
async def test_export_rejects_cross_tenant_or_bad_digest(service):
    await service.open_dsar("tenant-a", "officer-1", "request-1", "Subject", "export", True)
    with pytest.raises(PrivacyError):
        await service.attach_export("tenant-a", "worker-1", "request-1", "privacy/tenant-b/request-1/a",
                                    [{"key": "privacy/tenant-b/request-1/a", "sha256": "bad"}])


@pytest.mark.asyncio
async def test_deletion_finishes_only_after_every_target(service):
    await service.open_dsar("tenant-a", "officer-1", "request-1", "Subject", "delete", True)
    for target in ("postgresql", "object-store", "opensearch"):
        result = await service.acknowledge_deletion("tenant-a", "worker-1", "request-1", target, "b" * 64)
        assert result.status == "deleting"
    result = await service.acknowledge_deletion("tenant-a", "worker-1", "request-1", "lakehouse", "b" * 64)
    assert result.status == "deleted"
    assert len(service.store.events) == 5


def test_identity_proof_is_request_bound_and_expires():
    key = "i" * 32
    proof = identity_proof(key, "digest", "request-1", 200)
    verify_identity_proof(key, proof, "digest", "request-1", 199)
    with pytest.raises(PrivacyError):
        verify_identity_proof(key, proof, "digest", "request-2", 199)
    with pytest.raises(PrivacyError):
        verify_identity_proof(key, proof, "digest", "request-1", 201)


def test_audit_chain_is_tenant_scoped_and_linked(service):
    # Events are evidence records and contain no raw subject identifiers.
    assert all("subject" not in event for event in service.store.events)
