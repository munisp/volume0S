"""Tenant-scoped consent, DSAR, and append-only privacy evidence."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import re
import time
from typing import Any, Protocol

SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
PURPOSES = {"field-census", "campaign", "lead-conversion", "ai-processing", "service-delivery"}
BASES = {"consent", "contract", "legal-obligation", "legitimate-interest"}
TARGETS = {"postgresql", "object-store", "opensearch", "lakehouse"}


class PrivacyError(ValueError):
    pass


def identifier(value: str, label: str = "identifier") -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise PrivacyError(f"invalid {label}")
    return value


def identity_proof(key: str, subject_digest: str, request_id: str, expires_at_unix: int) -> str:
    """Bind identity verification to one subject/request; production uses a dedicated secret."""
    if len(key) < 32:
        raise PrivacyError("identity proof key is required")
    payload = f"{subject_digest}.{request_id}.{expires_at_unix}"
    signature = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_identity_proof(key: str, proof: str, subject_digest: str, request_id: str, now: int) -> None:
    try:
        proof_subject, proof_request, raw_expiry, signature = proof.rsplit(".", 3)
        expires = int(raw_expiry)
    except (ValueError, AttributeError) as exc:
        raise PrivacyError("malformed identity proof") from exc
    expected = identity_proof(key, proof_subject, proof_request, expires)
    if not hmac.compare_digest(proof, expected) or proof_subject != subject_digest or proof_request != request_id or expires < now:
        raise PrivacyError("identity proof is invalid, expired, or belongs to another request")


def canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class Consent:
    tenant_id: str
    reference: str
    subject_digest: str
    purpose: str
    lawful_basis: str
    status: str
    version: int
    actor: str
    created_at_unix: int


@dataclass(frozen=True)
class DSAR:
    tenant_id: str
    request_id: str
    subject_digest: str
    kind: str
    status: str
    actor: str
    created_at_unix: int
    export_manifest: dict[str, Any] | None = None
    deletion: dict[str, str] = field(default_factory=dict)


class PrivacyStore(Protocol):
    async def consent(self, tenant_id: str, reference: str) -> Consent | None: ...
    async def append_consent(self, consent: Consent) -> None: ...
    async def dsar(self, tenant_id: str, request_id: str) -> DSAR | None: ...
    async def create_dsar(self, request: DSAR) -> None: ...
    async def update_dsar(self, request: DSAR) -> None: ...
    async def audit(self, tenant_id: str, event: dict[str, Any]) -> None: ...


class MemoryPrivacyStore:
    """Test-only store; production construction always selects PostgreSQL/RLS."""
    def __init__(self):
        self.consents: dict[tuple[str, str], Consent] = {}
        self.requests: dict[tuple[str, str], DSAR] = {}
        self.events: list[dict[str, Any]] = []
        self.lock = asyncio.Lock()

    async def consent(self, tenant_id: str, reference: str) -> Consent | None:
        return self.consents.get((tenant_id, reference))

    async def append_consent(self, consent: Consent) -> None:
        async with self.lock:
            current = await self.consent(consent.tenant_id, consent.reference)
            if current and current.version >= consent.version:
                raise PrivacyError("consent version conflict")
            self.consents[(consent.tenant_id, consent.reference)] = consent

    async def dsar(self, tenant_id: str, request_id: str) -> DSAR | None:
        return self.requests.get((tenant_id, request_id))

    async def create_dsar(self, request: DSAR) -> None:
        async with self.lock:
            key = (request.tenant_id, request.request_id)
            if key in self.requests:
                raise PrivacyError("DSAR request already exists")
            self.requests[key] = request

    async def update_dsar(self, request: DSAR) -> None:
        async with self.lock:
            key = (request.tenant_id, request.request_id)
            if key not in self.requests:
                raise PrivacyError("DSAR request does not exist")
            self.requests[key] = request

    async def audit(self, tenant_id: str, event: dict[str, Any]) -> None:
        tenant_events = [item for item in self.events if item["tenant_id"] == tenant_id]
        previous = tenant_events[-1]["event_sha256"] if tenant_events else "0" * 64
        payload = {"tenant_id": tenant_id, **event, "previous_sha256": previous}
        payload["event_sha256"] = hashlib.sha256(canonical(payload).encode()).hexdigest()
        self.events.append(payload)


class PrivacyService:
    def __init__(self, store: PrivacyStore, subject_key: bytes, clock=time.time):
        if len(subject_key) < 32:
            raise ValueError("SUBJECT_HMAC_SECRET must contain at least 32 bytes")
        self.store = store
        self.subject_key = subject_key
        self.clock = clock

    def subject_digest(self, subject: str) -> str:
        normalized = " ".join(subject.strip().casefold().split())
        if len(normalized) < 2:
            raise PrivacyError("a verified DSAR/consent subject is required")
        return hmac.new(self.subject_key, normalized.encode(), hashlib.sha256).hexdigest()

    async def record_consent(self, tenant_id: str, actor: str, reference: str, subject: str,
                             purpose: str, lawful_basis: str, status: str = "granted") -> Consent:
        identifier(tenant_id, "tenant")
        identifier(actor, "actor")
        identifier(reference, "consent reference")
        if purpose not in PURPOSES or lawful_basis not in BASES or status not in {"granted", "withdrawn"}:
            raise PrivacyError("invalid consent purpose, lawful basis, or status")
        current = await self.store.consent(tenant_id, reference)
        if current and current.subject_digest != self.subject_digest(subject):
            raise PrivacyError("consent reference cannot be reassigned to another subject")
        consent = Consent(tenant_id, reference, self.subject_digest(subject), purpose, lawful_basis,
                          status, (current.version + 1) if current else 1, actor, int(self.clock()))
        await self.store.append_consent(consent)
        await self.store.audit(tenant_id, {"type": "consent", "reference": reference, "version": consent.version,
                                           "status": status, "purpose": purpose, "actor": actor})
        return consent

    async def open_dsar(self, tenant_id: str, actor: str, request_id: str, subject: str,
                        kind: str, identity_verified: bool) -> DSAR:
        identifier(tenant_id, "tenant")
        identifier(actor, "actor")
        identifier(request_id, "DSAR request")
        if kind not in {"export", "delete"} or identity_verified is not True:
            raise PrivacyError("DSAR requires export/delete scope and verified identity")
        request = DSAR(tenant_id, request_id, self.subject_digest(subject), kind, "open", actor, int(self.clock()))
        await self.store.create_dsar(request)
        await self.store.audit(tenant_id, {"type": "dsar-open", "request_id": request_id,
                                           "kind": kind, "actor": actor})
        return request

    async def attach_export(self, tenant_id: str, actor: str, request_id: str,
                            object_key: str, files: list[dict[str, str]]) -> DSAR:
        request = await self._kind(tenant_id, request_id, "export")
        prefix = f"privacy/{tenant_id}/{request_id}/"
        if not object_key.startswith(prefix) or ".." in object_key or not files:
            raise PrivacyError("export must use the tenant/request prefix and contain files")
        normalized = []
        for item in files:
            key, digest = item.get("key", ""), item.get("sha256", "")
            if not key.startswith(prefix) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise PrivacyError("invalid export file manifest")
            normalized.append({"key": key, "sha256": digest})
        manifest = {"tenant_id": tenant_id, "request_id": request_id, "files": normalized}
        manifest["manifest_sha256"] = hashlib.sha256(canonical(manifest).encode()).hexdigest()
        if request.status == "exported":
            if request.export_manifest == manifest:
                return request
            raise PrivacyError("export replay conflicts with the terminal manifest")
        if request.status == "deleted":
            raise PrivacyError("DSAR request is already terminal")
        updated = DSAR(**{**request.__dict__, "status": "exported", "export_manifest": manifest})
        await self.store.update_dsar(updated)
        await self.store.audit(tenant_id, {"type": "dsar-export", "request_id": request_id,
                                           "manifest_sha256": manifest["manifest_sha256"], "actor": actor})
        return updated

    async def acknowledge_deletion(self, tenant_id: str, actor: str, request_id: str,
                                   target: str, evidence_digest: str) -> DSAR:
        request = await self._kind(tenant_id, request_id, "delete")
        if target not in TARGETS or not re.fullmatch(r"[0-9a-f]{64}", evidence_digest):
            raise PrivacyError("invalid deletion target or evidence")
        deletion = {**request.deletion, target: evidence_digest}
        if request.status in {"deleted", "deleting"} and request.deletion.get(target):
            if request.deletion[target] == evidence_digest:
                return request
            raise PrivacyError("deletion replay conflicts with recorded evidence")
        if request.status == "deleted":
            raise PrivacyError("DSAR request is already terminal")
        status = "deleted" if set(deletion) == TARGETS else "deleting"
        updated = DSAR(**{**request.__dict__, "status": status, "deletion": deletion})
        await self.store.update_dsar(updated)
        await self.store.audit(tenant_id, {"type": "dsar-delete-target", "request_id": request_id,
                                           "target": target, "evidence_sha256": evidence_digest, "actor": actor})
        return updated

    async def _kind(self, tenant_id: str, request_id: str, kind: str) -> DSAR:
        request = await self.store.dsar(tenant_id, request_id)
        if request is None or request.kind != kind:
            raise PrivacyError("matching DSAR request not found")
        return request
