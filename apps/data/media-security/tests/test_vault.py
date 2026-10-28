import base64
import json

import httpx
import pytest

from envelope import MediaError
from vault import VaultTransitProvider


def test_vault_transit_wrap_unwrap_is_tenant_scoped_and_version_checked():
    requests = []
    plaintext = b"d" * 32
    wrapped = b"vault:v3:d3JhcHBlZC1rZXktbWF0ZXJpYWw="

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/encrypt/volumeos-tenant-tenant-a"):
            body = json.loads(request.content)
            assert base64.b64decode(body["plaintext"]) == plaintext
            assert request.headers["X-Vault-Token"] == "secret-token"
            return httpx.Response(200, json={"data": {"ciphertext": wrapped.decode()}})
        if request.url.path.endswith("/decrypt/volumeos-tenant-tenant-a"):
            assert json.loads(request.content)["ciphertext"] == wrapped.decode()
            return httpx.Response(200, json={"data": {"plaintext": base64.b64encode(plaintext).decode()}})
        return httpx.Response(403)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = VaultTransitProvider("https://vault.internal", "secret-token", client=client)
    key = provider.wrap("tenant-a", plaintext)
    assert key.version == 3
    assert key.ciphertext == wrapped
    assert provider.unwrap("tenant-a", 3, wrapped) == plaintext
    assert len(requests) == 2
    with pytest.raises(MediaError, match="version"):
        provider.unwrap("tenant-a", 2, wrapped)


def test_vault_rejects_invalid_scope_and_key_material():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": {}})))
    provider = VaultTransitProvider("https://vault.internal", "secret-token", client=client)
    with pytest.raises(MediaError):
        provider.wrap("../tenant", b"d" * 32)
    with pytest.raises(MediaError):
        provider.wrap("tenant-a", b"short")
    with pytest.raises(MediaError):
        provider.wrap("tenant-a", b"d" * 32)
