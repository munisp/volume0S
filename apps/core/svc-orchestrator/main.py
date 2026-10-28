"""Authenticated M1 campaign and Studio orchestration HTTP API."""
from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

import boto3
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from temporalio.client import Client
from temporalio.exceptions import TemporalError, WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from assets import QAAssetError, S3QAAssets
from auth import AuthenticationError, Claims, OIDCAuthenticator
from money import MoneyServiceError, MoneyServices
from workflows import TemporalWorkflows

ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
ResourceId = Annotated[str, Path(pattern=ID_PATTERN)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CampaignStart(InputModel):
    campaign_id: str = Field(pattern=ID_PATTERN)
    cycle_id: str = Field(pattern=ID_PATTERN)
    brief: str = Field(min_length=1, max_length=10_000)
    amount_minor: int = Field(gt=0, le=1_000_000_000_000)
    currency: Literal["NGN", "USD"] = "NGN"


class CampaignApproval(InputModel):
    approval_id: str = Field(min_length=1, max_length=128)


class StudioStart(InputModel):
    pack_id: str = Field(pattern=ID_PATTERN)
    product_id: str = Field(pattern=ID_PATTERN)
    tier: Literal["starter", "growth", "pro"]
    source_image: str = Field(min_length=1, max_length=1_024)
    checkpoint: str = Field(min_length=1, max_length=255)
    materials: str = Field(default="", max_length=500)
    dimensions: str = Field(default="", max_length=200)
    price_text: str = Field(default="", max_length=100)


class StudioReview(InputModel):
    decision: Literal["approve", "reject"]
    reason: str = Field(default="", max_length=1_000)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)


class OfferStart(InputModel):
    job_id: str = Field(pattern=ID_PATTERN)
    offer: Literal["03", "05", "07", "08"]
    amount_minor: int = Field(gt=0, le=1_000_000_000_000)
    currency: Literal["NGN", "USD"] = "NGN"
    payload: dict[str, Any]

    @model_validator(mode="after")
    def valid_budget(self):
        for field in ("cost_estimate_minor", "job_ceiling_minor"):
            value = self.payload.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field} must be positive exact minor units")
        return self


class OfferReview(InputModel):
    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=1, max_length=1_000)
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)


class PartyInput(InputModel):
    legal_name: str = Field(min_length=2, max_length=160)
    tin: str = Field(pattern=r"^\d{8,14}$")
    address: str = Field(min_length=2, max_length=300)


class BillingCycleStart(InputModel):
    subscription_id: str = Field(pattern=ID_PATTERN)
    offer: Literal["01", "02", "03", "04", "05", "06", "07", "08"]
    cycle: str = Field(pattern=r"^\d{4}-(?:0[1-9]|1[0-2])$")
    issue_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    retainer_minor: int = Field(gt=0, le=1_000_000_000_000)
    setup_minor: int = Field(default=0, ge=0, le=1_000_000_000_000)
    usage_minor: int = Field(default=0, ge=0, le=1_000_000_000_000)
    tax_minor: int = Field(default=0, ge=0, le=1_000_000_000_000)
    currency: Literal["NGN", "USD"] = "NGN"
    seller: PartyInput
    buyer: PartyInput
    billing_whatsapp: str = Field(pattern=r"^\+[1-9]\d{7,14}$")


class TopupStart(InputModel):
    amount_minor: int = Field(gt=0, le=1_000_000_000_000)
    currency: Literal["NGN", "USD"] = "NGN"
    customer_email: str = Field(min_length=3, max_length=320, pattern=r"^[^\s@]+@[^\s@]+$")
    redirect_url: str = Field(min_length=12, max_length=1_024, pattern=r"^https://")


class FundingRecovery(InputModel):
    subscription_id: str = Field(pattern=ID_PATTERN)
    cycle: str = Field(pattern=r"^\d{4}-(?:0[1-9]|1[0-2])$")
    provider: Literal["paystack", "flutterwave", "moniepoint"]
    reference: str = Field(pattern=ID_PATTERN)


class SuspenseResolutionStart(InputModel):
    tenant_id: str = Field(pattern=ID_PATTERN)
    reason: str = Field(min_length=3, max_length=1_000)


class ArExport(InputModel):
    as_of: date


class PartnerContractStart(InputModel):
    contract_id: str = Field(pattern=ID_PATTERN)
    partner_id: str = Field(pattern=ID_PATTERN)
    offer: Literal["01", "02", "03", "04", "05", "06", "07", "08"]
    basis_points: int = Field(ge=0, le=10_000)
    effective_from: datetime
    effective_to: datetime | None = None

    @model_validator(mode="after")
    def valid_period(self):
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        return self


class PartnerExport(InputModel):
    start: date
    end: date

    @model_validator(mode="after")
    def valid_period(self):
        if self.end < self.start or (self.end - self.start).days > 3660:
            raise ValueError("statement period must be valid and no longer than ten years")
        return self


@asynccontextmanager
async def lifespan(app: FastAPI):
    if app.state.workflows is None:
        client = await Client.connect(
            os.environ.get("TEMPORAL_HOSTPORT", "temporal-frontend.ops.svc.cluster.local:7233"),
            namespace=os.environ.get("TEMPORAL_NAMESPACE", "volumeos"),
        )
        app.state.workflows = TemporalWorkflows(client)
    if app.state.authenticator is None:
        app.state.authenticator = OIDCAuthenticator()
    if app.state.qa_assets is None:
        expiry = int(os.environ.get("QA_URL_EXPIRY_SECONDS", "300"))
        store = boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT_URL"))
        app.state.qa_assets = S3QAAssets(os.environ.get("ASSET_BUCKET", ""), store, expiry)
    if app.state.money is None and app.state.configure_money:
        app.state.money = MoneyServices.from_env()
    yield


def error(message: str, status: int, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


async def current_claims(request: Request) -> Claims:
    try:
        return await request.app.state.authenticator.authenticate(request.headers.get("Authorization"))
    except AuthenticationError as exc:
        raise error("Valid OIDC credentials and tenant scope are required.", 401, "unauthorized") from exc


def require(claims: Claims, role: str) -> None:
    if not claims.can(role):
        raise error(f"Required role: {role}.", 403, "forbidden")


def verify_snapshot(snapshot: dict[str, Any], claims: Claims, id_field: str, expected_id: str) -> None:
    if snapshot.get("tenant_id") != claims.tenant_id or snapshot.get(id_field) != expected_id:
        # Never expose whether another tenant's workflow exists.
        raise error("Workflow was not found in this tenant.", 404, "not_found")


def sorted_snapshots(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(snapshots, key=lambda item: str(item.get("updated_at", "")), reverse=True)


def create_app(workflows: Any = None, authenticator: Any = None, qa_assets: Any = None, money: Any = None) -> FastAPI:
    app = FastAPI(title="VolumeOS Orchestrator", version="0.4.0", lifespan=lifespan)
    app.state.workflows = workflows
    app.state.authenticator = authenticator
    app.state.qa_assets = qa_assets
    app.state.money = money
    app.state.configure_money = workflows is None

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        return JSONResponse({"error": detail}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _exc: RequestValidationError):
        return JSONResponse({"error": {"code": "invalid_request", "message": "Request fields are invalid."}}, status_code=400)

    @app.exception_handler(RPCError)
    async def workflow_rpc_error(_request: Request, exc: RPCError):
        if exc.status == RPCStatusCode.NOT_FOUND:
            return JSONResponse({"error": {"code": "not_found", "message": "Workflow was not found in this tenant."}}, status_code=404)
        return JSONResponse({"error": {"code": "orchestrator_unavailable", "message": "Workflow service is unavailable."}}, status_code=503)

    @app.exception_handler(TemporalError)
    async def temporal_unavailable(_request: Request, _exc: TemporalError):
        return JSONResponse({"error": {"code": "orchestrator_unavailable", "message": "Workflow service is unavailable."}}, status_code=503)

    @app.exception_handler(MoneyServiceError)
    async def money_error(_request: Request, exc: MoneyServiceError):
        return JSONResponse({"error": {"code": exc.code, "message": str(exc)}}, status_code=exc.status)

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready():
        try:
            await app.state.workflows.ready()
        except Exception as exc:
            raise error("Temporal dependency is not ready.", 503, "not_ready") from exc
        return {"status": "ready"}

    def money_services() -> Any:
        if app.state.money is None:
            raise error("Money services are not configured.", 503, "money_not_configured")
        return app.state.money

    @app.get("/v1/campaigns")
    async def campaigns(claims: Claims = Depends(current_claims)):
        require(claims, "reader")
        snapshots = await app.state.workflows.list(f"campaign:{claims.tenant_id}:")
        for snapshot in snapshots:
            if snapshot.get("tenant_id") != claims.tenant_id:
                raise error("Workflow tenant scope mismatch.", 403, "forbidden")
        return {"campaigns": sorted_snapshots(snapshots)}

    @app.post("/v1/campaigns", status_code=202)
    async def start_campaign(body: CampaignStart, claims: Claims = Depends(current_claims)):
        require(claims, "delivery")
        argument = body.model_dump() | {"tenant_id": claims.tenant_id}
        try:
            await app.state.workflows.start(
                "CampaignWorkflow", argument, f"campaign:{claims.tenant_id}:{body.campaign_id}", "campaigns-v1",
            )
        except WorkflowAlreadyStartedError as exc:
            raise error("Campaign ID already exists.", 409, "conflict") from exc
        return {"status": "accepted", "campaign_id": body.campaign_id}

    @app.post("/v1/campaigns/{campaign_id}/approval", status_code=202)
    async def approve_campaign(campaign_id: ResourceId, body: CampaignApproval, claims: Claims = Depends(current_claims)):
        require(claims, "delivery")
        workflow_id = f"campaign:{claims.tenant_id}:{campaign_id}"
        snapshot = await app.state.workflows.query(workflow_id)
        verify_snapshot(snapshot, claims, "campaign_id", campaign_id)
        if snapshot.get("status") != "awaiting_approval":
            raise error("Campaign is not awaiting human approval.", 409, "invalid_state")
        await app.state.workflows.signal(workflow_id, "approve", {
            "approval_id": body.approval_id, "approver": claims.actor,
        })
        return {"status": "accepted", "campaign_id": campaign_id}

    @app.get("/v1/studio/packs")
    async def studio_packs(claims: Claims = Depends(current_claims)):
        require(claims, "reader")
        snapshots = await app.state.workflows.list(f"studio:{claims.tenant_id}:")
        for snapshot in snapshots:
            if snapshot.get("tenant_id") != claims.tenant_id:
                raise error("Workflow tenant scope mismatch.", 403, "forbidden")
        return {"packs": sorted_snapshots(snapshots)}

    @app.post("/v1/studio/packs", status_code=202)
    async def start_studio_pack(body: StudioStart, claims: Claims = Depends(current_claims)):
        require(claims, "delivery")
        source = PurePosixPath(body.source_image)
        if (not body.source_image.startswith(f"{claims.tenant_id}/") or source.is_absolute()
                or ".." in source.parts):
            raise error("Source image must be inside the authenticated tenant prefix.", 400, "invalid_source")
        argument = body.model_dump() | {"tenant_id": claims.tenant_id}
        try:
            await app.state.workflows.start(
                "StudioWorkflow", argument, f"studio:{claims.tenant_id}:{body.pack_id}", "studio-v1",
            )
        except WorkflowAlreadyStartedError as exc:
            raise error("Studio pack ID already exists.", 409, "conflict") from exc
        return {"status": "accepted", "pack_id": body.pack_id}

    @app.get("/v1/studio/packs/{pack_id}/assets")
    async def studio_assets(pack_id: ResourceId, claims: Claims = Depends(current_claims)):
        require(claims, "reader")
        workflow_id = f"studio:{claims.tenant_id}:{pack_id}"
        snapshot = await app.state.workflows.query(workflow_id)
        verify_snapshot(snapshot, claims, "pack_id", pack_id)
        if snapshot.get("status") != "awaiting_qa":
            raise error("Studio pack is not awaiting human QA.", 409, "invalid_state")
        manifest_sha256 = snapshot.get("manifest_sha256", "")
        asset_count = snapshot.get("assets", 0)
        if not isinstance(manifest_sha256, str) or not isinstance(asset_count, int) or asset_count <= 0:
            raise error("QA assets are not ready for review.", 409, "assets_not_ready")
        try:
            assets = await app.state.qa_assets.review_assets(
                claims.tenant_id, pack_id, manifest_sha256, asset_count,
            )
        except QAAssetError as exc:
            raise error("QA assets failed validation and cannot be reviewed.", 409, "invalid_qa_assets") from exc
        return {"pack_id": pack_id, "manifest_sha256": manifest_sha256, "assets": assets}

    @app.post("/v1/studio/packs/{pack_id}/review", status_code=202)
    async def review_studio_pack(pack_id: ResourceId, body: StudioReview, claims: Claims = Depends(current_claims)):
        require(claims, "delivery")
        if body.decision == "reject" and not body.reason:
            raise error("A rejection reason is required.", 400, "invalid_request")
        workflow_id = f"studio:{claims.tenant_id}:{pack_id}"
        snapshot = await app.state.workflows.query(workflow_id)
        verify_snapshot(snapshot, claims, "pack_id", pack_id)
        if snapshot.get("status") != "awaiting_qa":
            raise error("Studio pack is not awaiting human QA.", 409, "invalid_state")
        if snapshot.get("manifest_sha256") != body.manifest_sha256:
            raise error("QA manifest changed; reload the assets before reviewing.", 409, "stale_manifest")
        try:
            await app.state.qa_assets.validate_manifest(
                claims.tenant_id, pack_id, body.manifest_sha256, snapshot.get("assets", 0),
            )
        except QAAssetError as exc:
            raise error("QA manifest validation failed; review is blocked.", 409, "invalid_qa_assets") from exc
        await app.state.workflows.signal(workflow_id, "review", {
            "decision": body.decision, "reason": body.reason,
            "reviewer": claims.actor, "manifest_sha256": body.manifest_sha256,
        })
        return {"status": "accepted", "pack_id": pack_id}

    @app.get("/v1/offers/jobs")
    async def offer_jobs(claims: Claims = Depends(current_claims)):
        require(claims, "reader")
        snapshots = await app.state.workflows.list(f"offer:{claims.tenant_id}:")
        for snapshot in snapshots:
            if snapshot.get("tenant_id") != claims.tenant_id:
                raise error("Workflow tenant scope mismatch.", 403, "forbidden")
        return {"jobs": sorted_snapshots(snapshots)}

    @app.post("/v1/offers/jobs", status_code=202)
    async def start_offer_job(body: OfferStart, claims: Claims = Depends(current_claims)):
        require(claims, "delivery")
        argument = body.model_dump() | {"tenant_id": claims.tenant_id}
        try:
            await app.state.workflows.start(
                "OfferWorkflow", argument, f"offer:{claims.tenant_id}:{body.job_id}", "offers-v1",
            )
        except WorkflowAlreadyStartedError as exc:
            raise error("Offer job ID already exists.", 409, "conflict") from exc
        return {"status": "accepted", "job_id": body.job_id}

    @app.get("/v1/offers/jobs/{job_id}")
    async def offer_job(job_id: ResourceId, claims: Claims = Depends(current_claims)):
        require(claims, "reader")
        snapshot = await app.state.workflows.query(f"offer:{claims.tenant_id}:{job_id}")
        verify_snapshot(snapshot, claims, "job_id", job_id)
        return snapshot

    @app.post("/v1/offers/jobs/{job_id}/review", status_code=202)
    async def review_offer_job(job_id: ResourceId, body: OfferReview, claims: Claims = Depends(current_claims)):
        if not (claims.can("delivery") or claims.can("analyst")):
            raise error("A delivery operator or analyst is required.", 403, "forbidden")
        workflow_id = f"offer:{claims.tenant_id}:{job_id}"
        snapshot = await app.state.workflows.query(workflow_id)
        verify_snapshot(snapshot, claims, "job_id", job_id)
        artifact = snapshot.get("artifact")
        if snapshot.get("status") != "awaiting-review" or not isinstance(artifact, dict):
            raise error("Offer job is not awaiting artifact review.", 409, "invalid_state")
        if artifact.get("sha256") != body.artifact_sha256:
            raise error("Artifact changed; reload the job before reviewing.", 409, "stale_artifact")
        await app.state.workflows.signal(workflow_id, "review", {
            "artifact_sha256": body.artifact_sha256, "reviewer": claims.actor,
            "decision": body.decision, "reason": body.reason,
            "reviewed_at_unix": int(datetime.now(timezone.utc).timestamp()),
        })
        return {"status": "accepted", "job_id": job_id}

    @app.get("/v1/billing/balance/{currency}")
    async def billing_balance(currency: Literal["NGN", "USD"], claims: Claims = Depends(current_claims)):
        require(claims, "reader")
        return await money_services().balance(claims.tenant_id, currency)

    @app.post("/v1/billing/topups/{provider}/{reference}", status_code=201)
    async def start_topup(provider: Literal["paystack", "flutterwave", "moniepoint"], reference: ResourceId,
                          body: TopupStart, claims: Claims = Depends(current_claims)):
        if not (claims.can("client-owner") or claims.can("delivery")):
            raise error("A client owner or delivery operator is required.", 403, "forbidden")
        payload = body.model_dump() | {"amount_minor": str(body.amount_minor)}
        return await money_services().topup(claims.tenant_id, provider, reference, payload)

    @app.get("/v1/billing/cycles")
    async def billing_cycles(claims: Claims = Depends(current_claims)):
        require(claims, "reader")
        snapshots = await app.state.workflows.list(f"billing:{claims.tenant_id}:")
        for snapshot in snapshots:
            if snapshot.get("tenant_id") != claims.tenant_id:
                raise error("Workflow tenant scope mismatch.", 403, "forbidden")
        return {"cycles": sorted_snapshots(snapshots)}

    @app.post("/v1/billing/cycles", status_code=202)
    async def start_billing_cycle(body: BillingCycleStart, claims: Claims = Depends(current_claims)):
        require(claims, "sales")
        argument = body.model_dump() | {
            "tenant_id": claims.tenant_id,
            # Pause/restore always reads a fresh optimistic tenancy version.
            "tenant_version": 1,
            "dunning_delays_seconds": [3 * 86400, 7 * 86400, 14 * 86400],
        }
        workflow_id = f"billing:{claims.tenant_id}:{body.subscription_id}:{body.cycle}"
        try:
            await app.state.workflows.start("BillingCycleWorkflow", argument, workflow_id, "billing-v1")
        except WorkflowAlreadyStartedError as exc:
            raise error("Billing cycle already exists.", 409, "conflict") from exc
        return {"status": "accepted", "subscription_id": body.subscription_id, "cycle": body.cycle}

    @app.get("/v1/billing/documents")
    async def billing_documents(claims: Claims = Depends(current_claims)):
        require(claims, "reader")
        return await money_services().documents(claims.tenant_id)

    @app.get("/v1/billing/documents/{kind}/{document_id}/pdf")
    async def billing_document_pdf(kind: Literal["invoice", "receipt"], document_id: ResourceId,
                                   claims: Claims = Depends(current_claims)):
        require(claims, "reader")
        content, number = await money_services().document_pdf(claims.tenant_id, kind, document_id)
        return Response(
            content=content, media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{number}.pdf"'},
        )

    @app.get("/v1/billing/reports/ar-aging")
    async def ar_aging_report(as_of: date | None = None, claims: Claims = Depends(current_claims)):
        require(claims, "reader")
        report_date = as_of or datetime.now(timezone.utc).date()
        return await money_services().ar_aging(claims.tenant_id, report_date.isoformat())

    @app.post("/v1/billing/reports/ar-aging/exports")
    async def export_ar_aging_report(body: ArExport, claims: Claims = Depends(current_claims)):
        require(claims, "finance")
        return await money_services().export_ar_aging(claims.tenant_id, body.as_of.isoformat())

    @app.get("/v1/billing/reports/partner-share/{partner_id}")
    async def partner_share_report(partner_id: ResourceId, start: date, end: date, claims: Claims = Depends(current_claims)):
        require(claims, "reader")
        if end < start or (end - start).days > 3660:
            raise error("Statement period must be valid and no longer than ten years.", 400, "invalid_request")
        return await money_services().partner_statement(claims.tenant_id, partner_id, start.isoformat(), end.isoformat())

    @app.post("/v1/billing/reports/partner-share/{partner_id}/exports")
    async def export_partner_share_report(partner_id: ResourceId, body: PartnerExport, claims: Claims = Depends(current_claims)):
        require(claims, "finance")
        return await money_services().export_partner_statement(
            claims.tenant_id, partner_id, body.start.isoformat(), body.end.isoformat(),
        )

    @app.get("/v1/billing/reports/trial-balance/{currency}")
    async def trial_balance_report(currency: Literal["NGN", "USD"], claims: Claims = Depends(current_claims)):
        require(claims, "finance")
        if claims.tenant_id != "platform":
            raise error("Platform finance scope is required.", 403, "forbidden")
        return await money_services().trial_balance(currency)

    @app.post("/v1/billing/reports/trial-balance/{currency}/exports")
    async def export_trial_balance_report(currency: Literal["NGN", "USD"], claims: Claims = Depends(current_claims)):
        require(claims, "finance")
        if claims.tenant_id != "platform":
            raise error("Platform finance scope is required.", 403, "forbidden")
        return await money_services().export_trial_balance(currency)

    @app.post("/v1/billing/partner-contracts", status_code=201)
    async def create_partner_contract(body: PartnerContractStart, claims: Claims = Depends(current_claims)):
        require(claims, "finance")
        return await money_services().create_partner_contract(claims.tenant_id, body.model_dump(mode="json"))

    @app.get("/v1/billing/partner-contracts")
    async def partner_contracts(partner_id: str | None = None, claims: Claims = Depends(current_claims)):
        require(claims, "reader")
        if partner_id is not None and not re.fullmatch(ID_PATTERN, partner_id):
            raise error("Invalid partner id.", 400, "invalid_request")
        return await money_services().partner_contracts(claims.tenant_id, partner_id)

    @app.get("/v1/billing/financial-exports/{kind}/{digest}")
    async def download_financial_export(
        kind: Literal["trial-balance", "ar-aging", "partner-share"],
        digest: Annotated[str, Path(pattern=SHA256_PATTERN)], claims: Claims = Depends(current_claims),
    ):
        require(claims, "reader")
        content, export_digest = await money_services().financial_export(claims.tenant_id, kind, digest)
        return Response(
            content=content, media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{kind}-{export_digest[:12]}.json"'},
        )

    @app.get("/v1/billing/collection-suspense")
    async def open_collection_suspense(claims: Claims = Depends(current_claims)):
        require(claims, "finance")
        if claims.tenant_id != "platform":
            raise error("Platform finance scope is required.", 403, "forbidden")
        return await money_services().open_suspense()

    @app.post("/v1/billing/collection-suspense/{provider}/{reference}/resolutions")
    async def resolve_collection_suspense(
        provider: Literal["paystack", "flutterwave", "moniepoint"], reference: ResourceId,
        body: SuspenseResolutionStart, claims: Claims = Depends(current_claims),
    ):
        require(claims, "finance")
        if claims.tenant_id != "platform":
            raise error("Platform finance scope is required.", 403, "forbidden")
        if body.tenant_id == "platform":
            raise error("A client tenant is required.", 400, "invalid_request")
        return await money_services().resolve_suspense(
            provider, reference, body.tenant_id, claims.actor, body.reason,
        )

    @app.post("/v1/billing/recovery", status_code=202)
    async def recover_billing(body: FundingRecovery, claims: Claims = Depends(current_claims)):
        require(claims, "delivery")
        workflow_id = f"billing:{claims.tenant_id}:{body.subscription_id}:{body.cycle}"
        snapshot = await app.state.workflows.query(workflow_id)
        if (snapshot.get("tenant_id") != claims.tenant_id
                or snapshot.get("subscription_id") != body.subscription_id
                or snapshot.get("cycle") != body.cycle):
            raise error("Billing cycle was not found in this tenant.", 404, "not_found")
        if snapshot.get("state") not in {"awaiting_funds", "paused"}:
            raise error("Billing cycle is not awaiting confirmed funds.", 409, "invalid_state")
        receipt = await money_services().collection_receipt(claims.tenant_id, body.provider, body.reference)
        await app.state.workflows.signal(workflow_id, "funding_recovered", {
            "recovery_reference": receipt["event_id"],
        })
        return {"status": "accepted", "recovery_reference": receipt["event_id"]}

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8080, workers=1)
