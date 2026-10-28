"""Shared offer artifact, approval, and budget invariants."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import re
import time
from typing import Any, Protocol

SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
OFFERS = {"03", "04", "05", "07", "08"}


class OfferError(ValueError):
    pass


def identifier(value: str, label: str = "identifier") -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise OfferError(f"invalid {label}")
    return value


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


@dataclass(frozen=True)
class Artifact:
    tenant_id: str
    offer: str
    job_id: str
    version: int
    object_key: str
    sha256: str
    media_type: str
    parent_sha256: str | None = None
    status: str = "draft"

    def validated(self) -> "Artifact":
        for value in (self.tenant_id, self.job_id):
            identifier(value)
        if self.offer not in OFFERS or not 1 <= self.version <= 10000:
            raise OfferError("unsupported offer or artifact version")
        prefix = f"{self.tenant_id}/offers/{self.offer}/{self.job_id}/"
        if not self.object_key.startswith(prefix) or ".." in self.object_key or not SHA256.fullmatch(self.sha256):
            raise OfferError("artifact key or digest is outside the tenant/job boundary")
        if self.parent_sha256 is not None and not SHA256.fullmatch(self.parent_sha256):
            raise OfferError("invalid parent artifact digest")
        if self.status not in {"draft", "reviewed", "released"} or self.media_type not in {
            "application/json", "application/pdf", "text/csv", "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        }:
            raise OfferError("invalid artifact status or media type")
        return self


@dataclass(frozen=True)
class Approval:
    artifact_sha256: str
    reviewer: str
    decision: str
    reason: str
    reviewed_at_unix: int

    def validated(self, artifact: Artifact) -> "Approval":
        identifier(self.reviewer, "reviewer")
        if self.artifact_sha256 != artifact.sha256 or self.decision not in {"approved", "rejected"} or not self.reason.strip():
            raise OfferError("approval must bind the exact artifact digest and include a reason")
        if self.reviewed_at_unix <= 0:
            raise OfferError("approval timestamp is required")
        return self


class ApprovalLedger(Protocol):
    async def append(self, tenant_id: str, job_id: str, approval: Approval) -> None: ...
    async def approvals(self, tenant_id: str, job_id: str, artifact_sha256: str) -> list[Approval]: ...


def release_artifact(artifact: Artifact, approvals: list[Approval], *, analysts: int = 1) -> Artifact:
    artifact.validated()
    if artifact.status == "released":
        return artifact
    matching = [approval.validated(artifact) for approval in approvals if approval.artifact_sha256 == artifact.sha256]
    if any(approval.decision == "rejected" for approval in matching):
        raise OfferError("rejected artifact cannot be released")
    reviewers = {approval.reviewer for approval in matching if approval.decision == "approved"}
    if len(reviewers) < analysts:
        raise OfferError(f"release requires {analysts} distinct reviewer approval(s)")
    return Artifact(**{**artifact.__dict__, "status": "released"})


class CostLedger(Protocol):
    async def spent_minor(self, tenant_id: str, job_id: str | None) -> int: ...
    async def reserve(self, tenant_id: str, job_id: str, amount_minor: int, idempotency_key: str) -> None: ...


class BudgetGuard:
    """Checks both per-job and per-tenant ceilings before any chargeable provider call."""
    def __init__(self, ledger: CostLedger, tenant_ceiling_minor: int):
        if tenant_ceiling_minor <= 0:
            raise OfferError("positive tenant budget ceiling required")
        self.ledger, self.tenant_ceiling = ledger, tenant_ceiling_minor

    async def before_provider_call(self, tenant_id: str, job_id: str, estimate_minor: int, job_ceiling_minor: int) -> None:
        identifier(tenant_id, "tenant"); identifier(job_id, "job")
        if isinstance(estimate_minor, bool) or not isinstance(estimate_minor, int) or estimate_minor <= 0 or job_ceiling_minor <= 0:
            raise OfferError("positive exact minor-unit estimate and job ceiling required")
        job_spent, tenant_spent = await self.ledger.spent_minor(tenant_id, job_id), await self.ledger.spent_minor(tenant_id, None)
        if job_spent + estimate_minor > job_ceiling_minor or tenant_spent + estimate_minor > self.tenant_ceiling:
            raise OfferError("AI/provider budget ceiling reached; provider call denied")
        await self.ledger.reserve(tenant_id, job_id, estimate_minor, f"ai-budget:{tenant_id}:{job_id}:{job_spent}:{estimate_minor}")


class RedactedAIClient:
    """External AI is reachable only through svc-privacy's redacted-completion route."""
    def __init__(self, transport, privacy_url: str, service_token: str):
        if not privacy_url.startswith("https://") or not service_token:
            raise OfferError("HTTPS privacy gateway and service token required")
        self.transport, self.url, self.token = transport, privacy_url.rstrip("/"), service_token

    async def complete(self, tenant_id: str, prompt: str, model: str, max_tokens: int = 4096) -> dict[str, Any]:
        response = await self.transport.post(self.url + "/v1/ai/redacted-completion",
            json={"prompt": prompt, "model": model, "max_tokens": max_tokens},
            headers={"Authorization": f"Bearer {self.token}", "X-VolumeOS-Tenant": tenant_id})
        response.raise_for_status()
        document = response.json()
        if not isinstance(document, dict):
            raise OfferError("redacted AI gateway returned a non-object response")
        return document
