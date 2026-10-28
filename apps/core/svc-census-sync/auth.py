"""OIDC RS256 authentication with mandatory tenant, actor, role, and device claims."""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class AuthenticationError(Exception):
    pass


@dataclass(frozen=True)
class Claims:
    tenant_id: str
    actor: str
    device_id: str
    roles: frozenset[str]

    def can_sync(self) -> bool:
        return bool(self.roles & {"field-agent", "super-operator"})


class OIDCAuthenticator:
    def __init__(self, issuer: str | None = None, audience: str | None = None,
                 jwks_url: str | None = None, http_client: httpx.AsyncClient | None = None):
        self.issuer = issuer if issuer is not None else os.environ.get("OIDC_ISSUER", "")
        self.audience = audience if audience is not None else os.environ.get("OIDC_AUDIENCE", "")
        self.jwks_url = jwks_url if jwks_url is not None else os.environ.get("OIDC_JWKS_URL", "")
        if not self.issuer or not self.audience or not self.jwks_url:
            raise RuntimeError("OIDC_ISSUER, OIDC_AUDIENCE, and OIDC_JWKS_URL are required")
        self._client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(5.0))
        self._keys: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    async def authenticate(self, authorization: str | None) -> Claims:
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthenticationError("a bearer token is required")
        token = authorization[7:].strip()
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise AuthenticationError("RS256 and a signing key ID are required")
            key = await self._key(header["kid"])
            payload = jwt.decode(
                token, key=key, algorithms=["RS256"], issuer=self.issuer, audience=self.audience,
                options={"require": ["exp", "tenant_id", "sub", "device_id"]},
            )
        except AuthenticationError:
            raise
        except (jwt.InvalidTokenError, httpx.HTTPError, ValueError, KeyError) as exc:
            raise AuthenticationError("valid OIDC credentials are required") from exc
        tenant_id, actor, device_id = payload.get("tenant_id"), payload.get("sub"), payload.get("device_id")
        roles = payload.get("roles", [])
        realm_access = payload.get("realm_access", {})
        realm_roles = realm_access.get("roles", []) if isinstance(realm_access, dict) else []
        if any(not isinstance(value, str) or not SAFE_ID.fullmatch(value)
               for value in (tenant_id, actor, device_id)):
            raise AuthenticationError("safe tenant, actor, and device claims are required")
        if not isinstance(roles, list) or not isinstance(realm_roles, list):
            raise AuthenticationError("role claims must be lists")
        role_set = frozenset(role for role in [*roles, *realm_roles] if isinstance(role, str) and role)
        if not role_set:
            raise AuthenticationError("at least one role is required")
        return Claims(tenant_id, actor, device_id, role_set)

    async def _key(self, kid: str) -> Any:
        if kid in self._keys:
            return self._keys[kid]
        async with self._lock:
            if kid in self._keys:
                return self._keys[kid]
            response = await self._client.get(self.jwks_url)
            response.raise_for_status()
            document = response.json()
            keys = document.get("keys", []) if isinstance(document, dict) else []
            refreshed: dict[str, Any] = {}
            for item in keys:
                if not isinstance(item, dict) or item.get("kty") != "RSA" or not item.get("kid"):
                    continue
                if item.get("use", "sig") != "sig" or item.get("alg", "RS256") != "RS256":
                    continue
                refreshed[str(item["kid"])] = jwt.PyJWK.from_dict(item).key
            self._keys = refreshed
        if kid not in self._keys:
            raise AuthenticationError("unknown signing key")
        return self._keys[kid]
