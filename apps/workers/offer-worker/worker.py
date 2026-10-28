"""Temporal batch workflows for offers 03/05/07/08; offer 04 uses its conversation API."""
from __future__ import annotations

import asyncio
import base64
from dataclasses import asdict, dataclass
from datetime import timedelta
import json
import os
from typing import Any

import asyncpg
import boto3
import httpx
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from books import DoclingClient, accounting_csv, accounting_csv_bytes
from common import Approval, Artifact, BudgetGuard, OfferError, RedactedAIClient, identifier, sha256
from compliance import BrowserAgentClient, Licence, paperwork, paperwork_payload
from costs import PostgresCostLedger
from funding import FundingJob, generate_draft, new_artifact, release_funding
from scorecards import WhisperXClient, build_report, release_report, report_payload, rubric_score
from books import release_export
from compliance import release_paperwork

BATCH_OFFERS = {"03", "05", "07", "08"}
_cost_ledger = None


@dataclass
class OfferJob:
    tenant_id: str
    job_id: str
    offer: str
    amount_minor: int
    currency: str
    payload: dict[str, Any]

    @property
    def ledger_event_id(self) -> str:
        return f"offer:{self.tenant_id}:{self.offer}:{self.job_id}:charge"


def validate_job(job: OfferJob) -> None:
    try:
        identifier(job.tenant_id, "tenant"); identifier(job.job_id, "job")
    except OfferError as exc:
        raise ApplicationError(str(exc), non_retryable=True) from exc
    if job.offer not in BATCH_OFFERS or isinstance(job.amount_minor, bool) or job.amount_minor <= 0 \
            or job.currency not in {"NGN", "USD"} or not isinstance(job.payload, dict):
        raise ApplicationError("offer, positive prepaid amount, currency, and payload are required", non_retryable=True)
    if len(json.dumps(job.payload)) > 200_000:
        raise ApplicationError("offer payload exceeds its bound", non_retryable=True)
    for field in ("cost_estimate_minor", "job_ceiling_minor"):
        value = job.payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ApplicationError(f"{field} must be positive exact minor units", non_retryable=True)


class S3ArtifactStore:
    def __init__(self):
        self.bucket = os.environ.get("ASSET_BUCKET", "")
        if not self.bucket: raise RuntimeError("ASSET_BUCKET is required")
        self.client = boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT_URL"))

    def put(self, artifact: Artifact, content: bytes) -> None:
        artifact.validated()
        if sha256(content) != artifact.sha256: raise OfferError("artifact content digest mismatch")
        self.client.put_object(Bucket=self.bucket, Key=artifact.object_key, Body=content, ContentType=artifact.media_type,
                               Metadata={"tenant-id": artifact.tenant_id, "artifact-sha256": artifact.sha256})
        self._manifest(artifact)

    def _manifest(self, artifact: Artifact) -> None:
        self.client.put_object(Bucket=self.bucket, Key=artifact.object_key + ".manifest.json",
                               Body=json.dumps(asdict(artifact), sort_keys=True).encode(), ContentType="application/json",
                               Metadata={"tenant-id": artifact.tenant_id, "artifact-sha256": artifact.sha256})

    def release(self, artifact: Artifact) -> None:
        if artifact.status != "released": raise OfferError("only reviewed artifacts can be released")
        self._manifest(artifact)


def store() -> S3ArtifactStore:
    if not hasattr(store, "instance"): store.instance = S3ArtifactStore()
    return store.instance


async def http() -> httpx.AsyncClient:
    if not hasattr(http, "instance"): http.instance = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
    return http.instance


async def budget(job: OfferJob) -> None:
    global _cost_ledger
    if _cost_ledger is None: _cost_ledger = await PostgresCostLedger.connect(os.environ.get("DATABASE_URL", ""))
    guard = BudgetGuard(_cost_ledger, int(os.environ.get("TENANT_AI_CEILING_MINOR", "0")))
    await guard.before_provider_call(job.tenant_id, job.job_id, job.payload["cost_estimate_minor"], job.payload["job_ceiling_minor"])


async def call_money(job: OfferJob, operation: str) -> dict[str, Any]:
    base = os.environ.get("BILLING_SERVICE_URL", "").rstrip("/")
    if not base: raise ApplicationError("BILLING_SERVICE_URL is not configured", non_retryable=True)
    if operation == "reserve":
        path, body = f"/v1/tenants/{job.tenant_id}/charges/reserve", {
            "event_id": job.ledger_event_id, "amount_minor": str(job.amount_minor), "currency": job.currency,
            "kind": "offer", "offer": job.offer, "channel": "ai", "timeout_seconds": 86400,
        }
    else:
        path, body = f"/v1/tenants/{job.tenant_id}/charges/{job.ledger_event_id}/{operation}", {}
    client = await http()
    response = await client.post(base + path, json=body, headers={"Idempotency-Key": f"{job.ledger_event_id}:{operation}",
        "X-VolumeOS-Tenant": job.tenant_id, "Authorization": f"Bearer {os.environ.get('VOLUMEOS_SERVICE_TOKEN', '')}"})
    response.raise_for_status(); result = response.json()
    expected = "pending" if operation == "reserve" else ("posted" if operation == "post" else "voided")
    if result.get("event_id") != job.ledger_event_id or result.get("tenant_id") != job.tenant_id or result.get("state") != expected:
        raise ApplicationError("ledger response does not match offer charge state", non_retryable=True)
    return result


@activity.defn
async def reserve_offer_charge(job: OfferJob) -> str:
    validate_job(job); await call_money(job, "reserve"); return job.ledger_event_id


@activity.defn
async def settle_offer_charge(job: OfferJob, operation: str) -> None:
    if operation not in {"post", "void"}: raise ApplicationError("invalid offer settlement", non_retryable=True)
    await call_money(job, operation)


@activity.defn
async def execute_offer(job: OfferJob) -> Artifact:
    validate_job(job); activity.heartbeat(); await budget(job)
    client, payload = await http(), job.payload
    if job.offer == "03":
        whisper = WhisperXClient(client, os.environ.get("WHISPERX_URL", ""), os.environ.get("VOLUMEOS_SERVICE_TOKEN", ""))
        transcript = await whisper.transcribe(job.tenant_id, job.job_id, payload["source_object_key"], payload["source_sha256"],
                                              payload["whisperx_version"], payload.get("language", "en"))
        scorecard = rubric_score(transcript, payload["rubric_version"], payload["rubric"], payload["scores"])
        artifact = build_report(transcript, scorecard, qa_reviewer=payload.get("qa_reviewer"), qa_reason=payload.get("qa_reason"))
        content = report_payload(transcript, scorecard, qa_reviewer=payload.get("qa_reviewer"), qa_reason=payload.get("qa_reason"))
    elif job.offer == "05":
        docling = DoclingClient(client, os.environ.get("DOCLING_URL", ""), os.environ.get("VOLUMEOS_SERVICE_TOKEN", ""))
        document = await docling.parse(job.tenant_id, job.job_id, payload["source_object_key"], payload["source_sha256"], payload["docling_version"])
        artifact = accounting_csv(document, payload["export_target"], reviewer=payload.get("reviewer"),
                                  review_reason=payload.get("review_reason"))
        content = accounting_csv_bytes(document)
    elif job.offer == "07":
        licence = Licence(job.tenant_id, job.job_id, payload["registry"], payload["registry_reference"],
                          payload["expires_on"], payload["portal_url"])
        browser = BrowserAgentClient(client, os.environ.get("BROWSER_AGENT_URL", ""), os.environ.get("VOLUMEOS_SERVICE_TOKEN", ""),
                                     set(os.environ.get("REGISTRY_ALLOWLIST", "").split(",")))
        check = await browser.check(licence, payload["evidence_object_key"])
        artifact = paperwork(licence, check, payload["values"])
        content = paperwork_payload(licence, check, payload["values"])
    else:
        funding = FundingJob(job.tenant_id, job.job_id, payload["kind"], payload["template_version"], payload["job_ceiling_minor"])
        ai = RedactedAIClient(client, os.environ.get("PRIVACY_SERVICE_URL", ""), os.environ.get("VOLUMEOS_SERVICE_TOKEN", ""))
        draft = await generate_draft(funding, ai, payload["source_material"], payload["model_version"])
        renderer = os.environ.get("FINANCE_TOOLKIT_URL", "").rstrip("/")
        if not renderer.startswith("https://"): raise OfferError("HTTPS finance toolkit is required")
        rendered = await client.post(renderer + "/v1/render", json={"kind": funding.kind, "template_version": funding.template_version,
            "draft": draft}, headers={"Authorization": f"Bearer {os.environ.get('VOLUMEOS_SERVICE_TOKEN', '')}",
            "Idempotency-Key": f"funding:{job.tenant_id}:{job.job_id}:v1", "X-VolumeOS-Tenant": job.tenant_id})
        rendered.raise_for_status(); document = rendered.json()
        content = base64.b64decode(document["content_base64"], validate=True)
        artifact = new_artifact(funding, content, 1)
    store().put(artifact, content)
    return artifact


@activity.defn
async def release_offer(job: OfferJob, artifact: Artifact, approvals: list[Approval]) -> Artifact:
    if job.offer == "03": released = release_report(artifact, approvals)
    elif job.offer == "05": released = release_export(artifact, approvals)
    elif job.offer == "07": released = release_paperwork(artifact, approvals)
    else: released = release_funding(artifact, approvals)
    store().release(released)
    return released


@workflow.defn
class OfferWorkflow:
    def __init__(self):
        self.status = "validating"
        self.tenant_id = ""
        self.job_id = ""
        self.offer = ""
        self.amount_minor = 0
        self.currency = ""
        self.artifact: Artifact | None = None
        self.approvals: list[Approval] = []
        self.started_at = ""
        self.release_complete = False

    @workflow.run
    async def run(self, job: OfferJob) -> Artifact:
        validate_job(job)
        self.tenant_id, self.job_id, self.offer = job.tenant_id, job.job_id, job.offer
        self.amount_minor, self.currency = job.amount_minor, job.currency
        self.started_at = workflow.now().isoformat(); self.status = "reserving"
        options = {"start_to_close_timeout": timedelta(minutes=5), "retry_policy": RetryPolicy(maximum_attempts=3)}
        await workflow.execute_activity(reserve_offer_charge, job, **options)
        try:
            self.status = "executing"
            self.artifact = await workflow.execute_activity(execute_offer, job, **options)
            self.status = "awaiting-review"
            required = 2 if job.offer == "08" else 1
            await workflow.wait_condition(lambda: self._review_complete(required), timeout=timedelta(days=30))
            if any(item.decision == "rejected" for item in self.approvals):
                self.status = "rejected"
                await workflow.execute_activity(settle_offer_charge, args=[job, "void"], **options)
                raise ApplicationError("offer artifact rejected", non_retryable=True)
            self.status = "releasing"
            released = await workflow.execute_activity(release_offer, args=[job, self.artifact, self.approvals], **options)
            self.release_complete = True
            await workflow.execute_activity(settle_offer_charge, args=[job, "post"], **options)
            self.status = "released"
            return released
        except BaseException:
            if self.status not in {"released", "rejected"} and not self.release_complete:
                self.status = "failed"
                await workflow.execute_activity(settle_offer_charge, args=[job, "void"], **options)
            raise

    def _review_complete(self, required: int) -> bool:
        if self.artifact is None: return False
        relevant = [item for item in self.approvals if item.artifact_sha256 == self.artifact.sha256]
        if any(item.decision == "rejected" for item in relevant): return True
        return len({item.reviewer for item in relevant if item.decision == "approved"}) >= required

    @workflow.signal
    def review(self, approval: Approval) -> None:
        if self.status != "awaiting-review" or self.artifact is None or approval.artifact_sha256 != self.artifact.sha256:
            raise ApplicationError("review does not match the current artifact", non_retryable=True)
        approval.validated(self.artifact)
        if any(item.reviewer == approval.reviewer for item in self.approvals):
            raise ApplicationError("reviewer already decided on this artifact", non_retryable=True)
        self.approvals.append(approval)

    @workflow.query(name="status")
    def current_status(self) -> dict[str, Any]:
        return {"tenant_id": self.tenant_id, "job_id": self.job_id, "offer": self.offer,
                "amount_minor": self.amount_minor, "currency": self.currency, "status": self.status,
                "artifact": asdict(self.artifact) if self.artifact else None,
                "approval_count": len(self.approvals), "started_at": self.started_at, "updated_at": self.started_at}


async def main() -> None:
    client = await Client.connect(os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
                                  namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"))
    worker = Worker(client, task_queue="offers-v1", workflows=[OfferWorkflow],
                    activities=[reserve_offer_charge, settle_offer_charge, execute_offer, release_offer])
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
