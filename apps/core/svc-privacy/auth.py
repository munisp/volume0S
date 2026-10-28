"""RS256 OIDC authentication for privacy operations."""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")


class AuthenticationError(Exception):
    pass


@dataclass(frozen=True)
class Claims:
    tenant_id: str
    actor: str
    roles: frozenset[str]

    def can_operate(self) -> bool:
        return bool(self.roles & {"super-operator", "privacy-officer"})

    def can_read(self) -> bool:
        return self.can_operate() or "auditor" in self.roles


Principal = Claims


def require_roles(claims: Claims, *roles: str) -> Claims:
    if not claims.roles.intersection(roles):
        from fastapi import HTTPException
        raise HTTPException(403, "required privacy role is missing")
    return claims


def oidc_from_env() -> "OIDCAuthenticator":
    return OIDCAuthenticator()


class OIDCAuthenticator:
    def __init__(self, issuer: str | None = None, audience: str | None = None,
                 jwks_url: str | None = None, http_client: httpx.AsyncClient | None = None):
        self.issuer = issuer if issuer is not None else os.environ.get("OIDC_ISSUER", "")
        self.audience = audience if audience is not None else os.environ.get("OIDC_AUDIENCE", "")
        self.jwks_url = jwks_url if jwks_url is not None else os.environ.get("OIDC_JWKS_URL", "")
        if not self.issuer or not self.audience or not self.jwks_url:
            raise RuntimeError("OIDC_ISSUER, OIDC_AUDIENCE, and OIDC_JWKS_URL are required")
        self.client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(5.0))
        self.keys: dict[str, Any] = {}
        self.lock = asyncio.Lock()

    async def authenticate(self, authorization: str | None) -> Claims:
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthenticationError("bearer credentials are required")
        token = authorization[7:].strip()
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise AuthenticationError("RS256 and key ID are required")
            key = await self._key(header["kid"])
            payload = jwt.decode(token, key=key, algorithms=["RS256"], issuer=self.issuer,
                                 audience=self.audience, options={"require": ["exp", "tenant_id", "sub"]})
        except AuthenticationError:
            raise
        except (jwt.InvalidTokenError, httpx.HTTPError, ValueError, KeyError) as exc:
            raise AuthenticationError("valid OIDC credentials are required") from exc
        tenant_id, actor = payload.get("tenant_id"), payload.get("sub")
        roles, realm = payload.get("roles", []), payload.get("realm_access", {})
        realm_roles = realm.get("roles", []) if isinstance(realm, dict) else []
        if (not isinstance(tenant_id, str) or not SAFE_ID.fullmatch(tenant_id)
                or not isinstance(actor, str) or not actor.strip()
                or not isinstance(roles, list) or not isinstance(realm_roles, list)):
            raise AuthenticationError("tenant, actor, and roles are required")
        role_set = frozenset(role for role in [*roles, *realm_roles] if isinstance(role, str) and role)
        if not role_set:
            raise AuthenticationError("at least one role is required")
        return Claims(tenant_id, actor, role_set)

    async def _key(self, kid: str) -> Any:
        if kid in self.keys:
            return self.keys[kid]
        async with self.lock:
            if kid in self.keys:
                return self.keys[kid]
            response = await self.client.get(self.jwks_url)
            response.raise_for_status()
            keys = response.json().get("keys", [])
            self.keys = {
                str(item["kid"]): jwt.PyJWK.from_dict(item).key
                for item in keys if isinstance(item, dict) and item.get("kty") == "RSA"
                and item.get("kid") and item.get("use", "sig") == "sig" and item.get("alg", "RS256") == "RS256"
            }
        if kid not in self.keys:
            raise AuthenticationError("unknown signing key")
        return self.keys[kid]
