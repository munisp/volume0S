"""Vault Transit adapter; plaintext DEKs are sent only to the configured KMS boundary."""
from __future__ import annotations

import base64
import os
import re

import httpx

from envelope import MediaError, SAFE_ID, WrappedKey

VAULT_KEY = re.compile(r"^vault:v([1-9][0-9]*):[A-Za-z0-9+/=]+$")


class VaultTransitProvider:
    def __init__(self, address: str, token: str, mount: str = "transit",
                 client: httpx.Client | None = None):
        if not address.startswith(("https://", "http://")) or not token or not SAFE_ID.fullmatch(mount):
            raise ValueError("Vault address, token, and safe mount are required")
        self.address = address.rstrip("/")
        self.mount = mount
        self.token = token
        self.client = client or httpx.Client(timeout=httpx.Timeout(10.0))

    @classmethod
    def from_env(cls) -> "VaultTransitProvider":
        return cls(os.environ.get("VAULT_ADDR", ""), os.environ.get("VAULT_TOKEN", ""),
                   os.environ.get("VAULT_TRANSIT_MOUNT", "transit"))

    def _key(self, tenant_id: str) -> str:
        if not SAFE_ID.fullmatch(tenant_id):
            raise MediaError("tenant ID is invalid for Vault key scoping")
        return f"volumeos-tenant-{tenant_id}"

    def wrap(self, tenant_id: str, plaintext_dek: bytes) -> WrappedKey:
        if len(plaintext_dek) != 32:
            raise MediaError("Vault wrapping requires a 32-byte DEK")
        response = self.client.post(
            f"{self.address}/v1/{self.mount}/encrypt/{self._key(tenant_id)}",
            json={"plaintext": base64.b64encode(plaintext_dek).decode()},
            headers={"X-Vault-Token": self.token},
        )
        response.raise_for_status()
        ciphertext = response.json().get("data", {}).get("ciphertext", "")
        match = VAULT_KEY.fullmatch(ciphertext)
        if not match:
            raise MediaError("Vault returned an invalid wrapped DEK")
        return WrappedKey(int(match.group(1)), ciphertext.encode())

    def unwrap(self, tenant_id: str, version: int, ciphertext: bytes) -> bytes:
        match = VAULT_KEY.fullmatch(ciphertext.decode("ascii", errors="strict"))
        if not match or int(match.group(1)) != version:
            raise MediaError("wrapped DEK version does not match its envelope")
        response = self.client.post(
            f"{self.address}/v1/{self.mount}/decrypt/{self._key(tenant_id)}",
            json={"ciphertext": ciphertext.decode()},
            headers={"X-Vault-Token": self.token},
        )
        response.raise_for_status()
        try:
            plaintext = base64.b64decode(response.json()["data"]["plaintext"], validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise MediaError("Vault returned an invalid plaintext DEK response") from exc
        if len(plaintext) != 32:
            raise MediaError("Vault returned a DEK with an invalid length")
        return plaintext
