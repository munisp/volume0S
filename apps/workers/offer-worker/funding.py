"""Offer 08: versioned model/memo/deck artifacts with two-analyst release."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common import Artifact, Approval, OfferError, RedactedAIClient, canonical, identifier, release_artifact, sha256

KINDS = {"model", "memo", "deck"}
MEDIA = {"model": "application/json", "memo": "application/pdf", "deck": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}


@dataclass(frozen=True)
class FundingJob:
    tenant_id: str
    job_id: str
    kind: str
    template_version: str
    budget_ceiling_minor: int

    def validated(self) -> "FundingJob":
        identifier(self.tenant_id, "tenant"); identifier(self.job_id, "job"); identifier(self.template_version, "template")
        if self.kind not in KINDS or isinstance(self.budget_ceiling_minor, bool) or self.budget_ceiling_minor <= 0:
            raise OfferError("funding kind, template, and positive budget ceiling required")
        return self


def new_artifact(job: FundingJob, content: bytes, version: int, parent: Artifact | None = None) -> Artifact:
    job.validated()
    if not content or len(content) > 25 * 1024 * 1024:
        raise OfferError("funding artifact is empty or exceeds its bound")
    if parent is not None:
        parent.validated()
        if (parent.tenant_id, parent.offer, parent.job_id) != (job.tenant_id, "08", job.job_id) or version != parent.version + 1:
            raise OfferError("funding version must extend the same job by exactly one version")
        parent_digest = parent.sha256
    elif version != 1:
        parent_digest = None
        raise OfferError("first funding artifact must be version one")
    else:
        parent_digest = None
    extension = {"model": "json", "memo": "pdf", "deck": "pptx"}[job.kind]
    return Artifact(job.tenant_id, "08", job.job_id, version,
                    f"{job.tenant_id}/offers/08/{job.job_id}/{job.kind}-v{version}.{extension}",
                    sha256(content), MEDIA[job.kind], parent_digest).validated()


def release_funding(artifact: Artifact, approvals: list[Approval]) -> Artifact:
    if artifact.offer != "08":
        raise OfferError("not a funding artifact")
    # Two distinct human analysts must approve the exact digest; generator output is never sufficient.
    return release_artifact(artifact, approvals, analysts=2)


async def generate_draft(job: FundingJob, ai: RedactedAIClient, source_material: str, model: str) -> dict[str, Any]:
    job.validated()
    if not source_material.strip() or len(source_material) > 200_000:
        raise OfferError("funding source material is required and bounded")
    prompt = f"Use template {job.template_version} to create a {job.kind} draft. Source:\n{source_material}"
    # This client only calls svc-privacy; raw source material never goes directly to an external provider.
    return await ai.complete(job.tenant_id, prompt, model)
