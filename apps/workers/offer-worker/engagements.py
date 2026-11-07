"""Approval-gated, plan-only delivery engagements for catalog offers 27–34.

This module deliberately has no subscription, money, provider-delivery, publishing,
customer-communication, or deployment path. Its only side effect is writing a
reviewable JSON plan and manifest to the authenticated tenant's object prefix.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from typing import Any

import boto3
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

from common import OfferError, canonical, identifier, sha256

ENGAGEMENT_OFFERS = frozenset({"27", "28", "29", "30", "31", "32", "33", "34"})
ENGAGEMENT_CONTROLS: dict[str, tuple[str, ...]] = {
    "27": (
        "Confirm content ownership, brand approvals, and target-page scope.",
        "Review source licenses, accessibility, privacy, and security before implementation.",
        "Require human approval of the proposed redesign; no website publication is permitted.",
    ),
    "28": (
        "Confirm rights and consent for every brand, person, asset, and music source.",
        "Require human creative, factual, and claims review before any later production request.",
        "No advertisement rendering, upload, targeting, or serving is permitted.",
    ),
    "29": (
        "Confirm curriculum sources, licensing, learner safeguards, and assessment scope.",
        "Require qualified human review of tutoring content and factual claims.",
        "No learner-data export, tutor deployment, or student communication is permitted.",
    ),
    "30": (
        "Define least-privilege roles, tool allowlists, data boundaries, and stop controls.",
        "Require human security and operations approval before any later installation request.",
        "No agent installation, credential issuance, tool invocation, or autonomous action is permitted.",
    ),
    "31": (
        "Require traceable dimensions, material assumptions, and qualified engineering review.",
        "Mark fit, load, tolerances, and safety constraints as unverified until independently validated.",
        "No CAD generation, fabrication, procurement, or safety certification is permitted.",
    ),
    "32": (
        "Confirm source provenance, usage rights, editorial scope, and factual-review ownership.",
        "Require human review of the briefing outline and cited-source plan.",
        "No audio generation, distribution, or publication is permitted.",
    ),
    "33": (
        "Define eligible procurement sources, verification ownership, and deadline checks.",
        "Require human review of bid qualifications and conflict-of-interest controls.",
        "No bid retrieval, buyer contact, representation, or tender submission is permitted.",
    ),
    "34": (
        "Require written authorization, owned-domain scope, safe check limits, and escalation contacts.",
        "Require human approval of monitoring objectives and incident-handling boundaries.",
        "No active scanning, monitoring activation, alert dispatch, or configuration change is permitted.",
    ),
}
PROHIBITED_ACTIONS = (
    "subscription activation",
    "invoice, charge, reservation, settlement, or ledger posting",
    "external provider execution or deployment",
    "customer communication or publication",
    "fabrication, procurement, or tender submission",
)


@dataclass(frozen=True)
class EngagementJob:
    tenant_id: str
    engagement_id: str
    offer: str
    title: str
    scope: str
    requested_outcome: str = ""


@dataclass(frozen=True)
class EngagementPlan:
    tenant_id: str
    engagement_id: str
    offer: str
    version: int
    object_key: str
    sha256: str
    media_type: str = "application/json"
    status: str = "draft"

    def validated(self) -> "EngagementPlan":
        identifier(self.tenant_id, "tenant")
        identifier(self.engagement_id, "engagement")
        expected_key = (
            f"{self.tenant_id}/engagements/{self.offer}/{self.engagement_id}/plan-v{self.version}.json"
        )
        if (
            self.offer not in ENGAGEMENT_OFFERS
            or self.version != 1
            or self.object_key != expected_key
            or len(self.sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.sha256)
            or self.media_type != "application/json"
            or self.status != "draft"
        ):
            raise OfferError("engagement plan is outside its tenant, offer, digest, or review boundary")
        return self


@dataclass(frozen=True)
class GeneratedEngagementPlan:
    artifact: EngagementPlan
    document: dict[str, Any]


@dataclass(frozen=True)
class EngagementReview:
    plan_sha256: str
    reviewer: str
    decision: str
    reason: str
    reviewed_at_unix: int

    def validated(self, plan: EngagementPlan) -> "EngagementReview":
        if (
            self.plan_sha256 != plan.sha256
            or self.decision not in {"approved", "rejected"}
            or not isinstance(self.reason, str)
            or not self.reason.strip()
            or len(self.reason) > 1_000
            or not isinstance(self.reviewer, str)
            or not self.reviewer.strip()
            or len(self.reviewer) > 128
            or any(ord(character) < 32 for character in self.reviewer)
            or isinstance(self.reviewed_at_unix, bool)
            or not isinstance(self.reviewed_at_unix, int)
            or self.reviewed_at_unix <= 0
        ):
            raise OfferError("review must bind the exact plan digest, reviewer, decision, reason, and timestamp")
        return self


def validate_engagement(job: EngagementJob) -> None:
    try:
        identifier(job.tenant_id, "tenant")
        identifier(job.engagement_id, "engagement")
    except OfferError as exc:
        raise ApplicationError(str(exc), non_retryable=True) from exc
    if (
        job.offer not in ENGAGEMENT_OFFERS
        or not isinstance(job.title, str)
        or not job.title.strip()
        or len(job.title) > 160
        or not isinstance(job.scope, str)
        or not job.scope.strip()
        or len(job.scope) > 10_000
        or not isinstance(job.requested_outcome, str)
        or len(job.requested_outcome) > 5_000
    ):
        raise ApplicationError("a Volume III offer, bounded title, scope, and outcome are required", non_retryable=True)


def new_engagement_plan(job: EngagementJob) -> GeneratedEngagementPlan:
    """Build deterministic content; retries produce the same digest and object key."""
    validate_engagement(job)
    document = {
        "schema_version": "volumeos.engagement-plan.v1",
        "tenant_id": job.tenant_id,
        "engagement_id": job.engagement_id,
        "offer": job.offer,
        "title": job.title.strip(),
        "scope": job.scope.strip(),
        "requested_outcome": job.requested_outcome.strip(),
        "commercial_status": "source-only-unpriced",
        "delivery_mode": "plan-only",
        "mandatory_controls": list(ENGAGEMENT_CONTROLS[job.offer]),
        "prohibited_actions": list(PROHIBITED_ACTIONS),
        "approval_requirement": "An authorized human must approve this exact plan digest before any separately scoped implementation request.",
        "acceptance_gate": "Approval of this plan does not activate delivery, commercial terms, external integrations, or production deployment.",
    }
    content = canonical(document).encode("utf-8")
    artifact = EngagementPlan(
        tenant_id=job.tenant_id,
        engagement_id=job.engagement_id,
        offer=job.offer,
        version=1,
        object_key=f"{job.tenant_id}/engagements/{job.offer}/{job.engagement_id}/plan-v1.json",
        sha256=sha256(content),
    ).validated()
    return GeneratedEngagementPlan(artifact=artifact, document=document)


class S3EngagementPlanStore:
    def __init__(self):
        self.bucket = os.environ.get("ASSET_BUCKET", "")
        if not self.bucket:
            raise RuntimeError("ASSET_BUCKET is required")
        self.client = boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT_URL"))

    def put(self, generated: GeneratedEngagementPlan) -> None:
        artifact = generated.artifact.validated()
        content = canonical(generated.document).encode("utf-8")
        if sha256(content) != artifact.sha256:
            raise OfferError("engagement plan content digest mismatch")
        self.client.put_object(
            Bucket=self.bucket,
            Key=artifact.object_key,
            Body=content,
            ContentType=artifact.media_type,
            Metadata={"tenant-id": artifact.tenant_id, "plan-sha256": artifact.sha256},
        )
        self.client.put_object(
            Bucket=self.bucket,
            Key=artifact.object_key + ".manifest.json",
            Body=json.dumps(asdict(artifact), sort_keys=True).encode("utf-8"),
            ContentType="application/json",
            Metadata={"tenant-id": artifact.tenant_id, "plan-sha256": artifact.sha256},
        )


def plan_store() -> S3EngagementPlanStore:
    if not hasattr(plan_store, "instance"):
        plan_store.instance = S3EngagementPlanStore()
    return plan_store.instance


@activity.defn
async def build_engagement_plan(job: EngagementJob) -> GeneratedEngagementPlan:
    generated = new_engagement_plan(job)
    await asyncio.to_thread(plan_store().put, generated)
    return generated


@workflow.defn
class EngagementWorkflow:
    """Durable plan/approval state; approval never triggers external delivery."""

    def __init__(self):
        self.tenant_id = ""
        self.engagement_id = ""
        self.offer = ""
        self.title = ""
        self.status = "validating"
        self.plan: EngagementPlan | None = None
        self.document: dict[str, Any] | None = None
        self.review_decision: EngagementReview | None = None
        self.started_at = ""
        self.updated_at = ""

    @workflow.run
    async def run(self, job: EngagementJob) -> dict[str, Any]:
        validate_engagement(job)
        self.tenant_id = job.tenant_id
        self.engagement_id = job.engagement_id
        self.offer = job.offer
        self.title = job.title.strip()
        self.started_at = workflow.now().isoformat()
        self.updated_at = self.started_at
        self.status = "planning"
        generated = await workflow.execute_activity(
            build_engagement_plan,
            job,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        self.plan = generated.artifact
        self.document = generated.document
        self.status = "awaiting-approval"
        decided = await workflow.wait_condition(
            lambda: self.review_decision is not None,
            timeout=timedelta(days=30),
        )
        if not decided:
            self.status = "approval-expired"
            self.updated_at = workflow.now().isoformat()
            raise ApplicationError("engagement plan approval expired", non_retryable=True)
        self.status = self.review_decision.decision
        return self.current_status()

    @workflow.signal
    def review(self, decision: EngagementReview) -> None:
        if self.status != "awaiting-approval" or self.plan is None:
            raise ApplicationError("engagement is not awaiting plan approval", non_retryable=True)
        try:
            decision.validated(self.plan)
        except OfferError as exc:
            raise ApplicationError(str(exc), non_retryable=True) from exc
        if self.review_decision is not None:
            raise ApplicationError("engagement plan already has a terminal review", non_retryable=True)
        self.review_decision = decision
        self.status = decision.decision
        self.updated_at = datetime.fromtimestamp(decision.reviewed_at_unix, timezone.utc).isoformat()

    @workflow.query(name="status")
    def current_status(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "engagement_id": self.engagement_id,
            "offer": self.offer,
            "title": self.title,
            "status": self.status,
            "commercial_status": "source-only-unpriced",
            "delivery_mode": "plan-only",
            "charges_enabled": False,
            "external_actions_enabled": False,
            "plan": asdict(self.plan) if self.plan else None,
            "document": self.document,
            "review": asdict(self.review_decision) if self.review_decision else None,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
        }
