import json

import pytest

from envelope import MediaError, TenantMediaCipher, WrappedKey


class Provider:
    def __init__(self):
        self.version = 0
        self.wrapped = []

    def wrap(self, tenant_id, plaintext_dek):
        self.version += 1
        mask = bytes([self.version]) * 32
        ciphertext = bytes(left ^ right for left, right in zip(plaintext_dek, mask))
        self.wrapped.append((tenant_id, self.version, ciphertext))
        return WrappedKey(self.version, ciphertext)

    def unwrap(self, tenant_id, version, ciphertext):
        expected = next(item for item in self.wrapped if item == (tenant_id, version, ciphertext))
        mask = bytes([expected[1]]) * 32
        return bytes(value ^ mask_byte for value, mask_byte in zip(ciphertext, mask))


def test_per_tenant_envelope_round_trip_and_no_plaintext_key_persistence():
    provider = Provider()
    cipher = TenantMediaCipher(provider)
    first = cipher.seal("tenant-a", "pack:image:1", b"png-media")
    document = json.loads(first)
    assert document["tenant_id"] == "tenant-a"
    assert document["key_version"] == 1
    assert b"png-media" not in first
    assert len(document["wrapped_dek"]) > 0
    assert all(item[2] != b"\x00" * 32 for item in provider.wrapped)
    assert cipher.open("tenant-a", "pack:image:1", first) == b"png-media"
    second = cipher.seal("tenant-a", "pack:image:1", b"png-media")
    assert first != second
    assert json.loads(second)["wrapped_dek"] != document["wrapped_dek"]


def test_cross_tenant_object_substitution_and_tampering_fail_closed():
    cipher = TenantMediaCipher(Provider())
    envelope = cipher.seal("tenant-a", "object-a", b"private-media")
    with pytest.raises(MediaError, match="does not match"):
        cipher.open("tenant-b", "object-a", envelope)
    with pytest.raises(MediaError, match="does not match"):
        cipher.open("tenant-a", "object-b", envelope)
    document = json.loads(envelope)
    document["ciphertext"] = document["ciphertext"][:-4] + "AAAA"
    with pytest.raises(MediaError):
        cipher.open("tenant-a", "object-a", json.dumps(document).encode())


def test_rotation_rewraps_without_changing_media_ciphertext():
    provider = Provider()
    cipher = TenantMediaCipher(provider)
    original = cipher.seal("tenant-a", "object-a", b"stable-media")
    before = json.loads(original)
    rotated = cipher.rotate("tenant-a", "object-a", original)
    after = json.loads(rotated)
    assert after["key_version"] > before["key_version"]
    assert after["wrapped_dek"] != before["wrapped_dek"]
    assert after["ciphertext"] == before["ciphertext"]
    assert after["nonce"] == before["nonce"]
    assert cipher.open("tenant-a", "object-a", rotated) == b"stable-media"


@pytest.mark.parametrize("tenant_id,object_id,size", [
    ("../tenant", "object", 1), ("tenant", "../object", 1), ("tenant", "object", 0),
    ("tenant", "object", 20 * 1024 * 1024 + 1),
])
def test_media_scope_and_size_bounds(tenant_id, object_id, size):
    with pytest.raises(MediaError):
        TenantMediaCipher(Provider()).seal(tenant_id, object_id, b"x" * size)
