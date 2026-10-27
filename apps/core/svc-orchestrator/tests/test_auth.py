import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from auth import AuthenticationError, OIDCAuthenticator


PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_JWK = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(PRIVATE_KEY.public_key()))
PUBLIC_JWK.update({"kid": "key-1", "use": "sig", "alg": "RS256"})


class JWKSClient:
    async def get(self, url):
        return httpx.Response(200, json={"keys": [PUBLIC_JWK]}, request=httpx.Request("GET", url))


def authenticator():
    return OIDCAuthenticator("https://identity.example/realms/volumeos", "core-services",
                             "https://identity.example/jwks", JWKSClient())


def token(**changes):
    claims = {"iss": "https://identity.example/realms/volumeos", "aud": "core-services",
              "sub": "operator-1", "tenant_id": "tenant-a", "roles": ["delivery"],
              "realm_access": {"roles": ["analyst"]}, "exp": int(time.time()) + 300}
    claims.update(changes)
    return jwt.encode(claims, PRIVATE_KEY, algorithm="RS256", headers={"kid": "key-1"})


async def test_rs256_oidc_claims_provide_tenant_actor_and_roles():
    claims = await authenticator().authenticate(f"Bearer {token()}")
    assert claims.tenant_id == "tenant-a"
    assert claims.actor == "operator-1"
    assert claims.can("delivery")
    assert claims.can("reader")


@pytest.mark.parametrize("changes", [
    {"exp": int(time.time()) - 60},
    {"aud": "another-service"},
    {"iss": "https://attacker.example"},
    {"tenant_id": "../other"},
    {"sub": ""},
    {"roles": [], "realm_access": {"roles": []}},
])
async def test_invalid_or_incomplete_oidc_claims_fail_closed(changes):
    with pytest.raises(AuthenticationError):
        await authenticator().authenticate(f"Bearer {token(**changes)}")


async def test_non_rs256_token_is_rejected_before_key_use():
    unsigned = jwt.encode({"sub": "operator-1"}, key="", algorithm="none", headers={"kid": "key-1"})
    with pytest.raises(AuthenticationError):
        await authenticator().authenticate(f"Bearer {unsigned}")
