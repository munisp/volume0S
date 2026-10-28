"""Per-tenant AES-GCM media envelopes backed by externally wrapped data keys."""
from __future__ import annotations

import base64
import json
import re
import secrets
from dataclasses import dataclass
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_MEDIA_BYTES = 20 * 1024 * 1024


class MediaError(ValueError):
    pass


@dataclass(frozen=True)
class WrappedKey:
    version: int
    ciphertext: bytes


class KeyProvider(Protocol):
    def wrap(self, tenant_id: str, plaintext_dek: bytes) -> WrappedKey: ...
    def unwrap(self, tenant_id: str, version: int, ciphertext: bytes) -> bytes: ...


def b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def unb64(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise MediaError("media envelope contains invalid base64") from exc


def associated_data(tenant_id: str, object_id: str) -> bytes:
    if not SAFE_ID.fullmatch(tenant_id) or not SAFE_ID.fullmatch(object_id):
        raise MediaError("tenant and object IDs must be safe bounded identifiers")
    return json.dumps({"tenant_id": tenant_id, "object_id": object_id}, sort_keys=True,
                      separators=(",", ":")).encode()


class TenantMediaCipher:
    """Only wrapped DEKs are serialized; plaintext DEKs never enter the envelope."""

    def __init__(self, provider: KeyProvider):
        self.provider = provider

    def seal(self, tenant_id: str, object_id: str, plaintext: bytes) -> bytes:
        if not 1 <= len(plaintext) <= MAX_MEDIA_BYTES:
            raise MediaError("media payload exceeds the reviewed size bound")
        aad = associated_data(tenant_id, object_id)
        dek = bytearray(secrets.token_bytes(32))
        try:
            wrapped = self.provider.wrap(tenant_id, bytes(dek))
            if wrapped.version < 1 or not wrapped.ciphertext:
                raise MediaError("key provider returned an invalid wrapped key")
            nonce = secrets.token_bytes(12)
            ciphertext = AESGCM(bytes(dek)).encrypt(nonce, plaintext, aad)
            document = {
                "format": "volumeos.media.v1", "tenant_id": tenant_id, "object_id": object_id,
                "key_version": wrapped.version, "wrapped_dek": b64(wrapped.ciphertext),
                "nonce": b64(nonce), "ciphertext": b64(ciphertext),
            }
            return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        finally:
            for index in range(len(dek)):
                dek[index] = 0

    def open(self, tenant_id: str, object_id: str, envelope: bytes) -> bytes:
        document = self._document(envelope)
        if document.get("format") != "volumeos.media.v1" or document.get("tenant_id") != tenant_id \
                or document.get("object_id") != object_id:
            raise MediaError("media envelope tenant, object, or format does not match")
        aad = associated_data(tenant_id, object_id)
        version = document.get("key_version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise MediaError("media key version is invalid")
        nonce, ciphertext = unb64(document["nonce"]), unb64(document["ciphertext"])
        if len(nonce) != 12 or not 17 <= len(ciphertext) <= MAX_MEDIA_BYTES + 16:
            raise MediaError("media nonce or ciphertext size is invalid")
        dek = bytearray(self.provider.unwrap(tenant_id, version, unb64(document["wrapped_dek"])))
        try:
            if len(dek) != 32:
                raise MediaError("unwrapped media key must contain 32 bytes")
            try:
                return AESGCM(bytes(dek)).decrypt(nonce, ciphertext, aad)
            except InvalidTag as exc:
                raise MediaError("media authentication failed") from exc
        finally:
            for index in range(len(dek)):
                dek[index] = 0

    def rotate(self, tenant_id: str, object_id: str, envelope: bytes) -> bytes:
        """Rewrap the same DEK under the tenant's current key without changing media ciphertext."""
        document = self._document(envelope)
        if document.get("tenant_id") != tenant_id or document.get("object_id") != object_id:
            raise MediaError("media rotation scope does not match")
        # Authentication is checked before replacing any key material.
        self.open(tenant_id, object_id, envelope)
        old_version = document["key_version"]
        dek = bytearray(self.provider.unwrap(tenant_id, old_version, unb64(document["wrapped_dek"])))
        try:
            wrapped = self.provider.wrap(tenant_id, bytes(dek))
            if wrapped.version <= old_version:
                raise MediaError("key provider did not advance the tenant key version")
            document["key_version"] = wrapped.version
            document["wrapped_dek"] = b64(wrapped.ciphertext)
            return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        finally:
            for index in range(len(dek)):
                dek[index] = 0

    @staticmethod
    def _document(envelope: bytes) -> dict:
        if not 1 <= len(envelope) <= MAX_MEDIA_BYTES * 2:
            raise MediaError("media envelope exceeds its size bound")
        try:
            value = json.loads(envelope)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MediaError("media envelope must be UTF-8 JSON") from exc
        expected = {"format", "tenant_id", "object_id", "key_version", "wrapped_dek", "nonce", "ciphertext"}
        if not isinstance(value, dict) or set(value) != expected:
            raise MediaError("media envelope fields are missing or unsupported")
        if any(not isinstance(value.get(name), str) for name in expected - {"key_version"}):
            raise MediaError("media envelope field types are invalid")
        return value
