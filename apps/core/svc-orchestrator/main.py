"""Authenticated M1 campaign and Studio orchestration HTTP API."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

import boto3
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from temporalio.client import Client
from temporalio.exceptions import TemporalError, WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from assets import QAAssetError, S3QAAssets
from auth import AuthenticationError, Claims, OIDCAuthenticator
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


def create_app(workflows: Any = None, authenticator: Any = None, qa_assets: Any = None) -> FastAPI:
    app = FastAPI(title="VolumeOS M1 Orchestrator", version="0.3.0", lifespan=lifespan)
    app.state.workflows = workflows
    app.state.authenticator = authenticator
    app.state.qa_assets = qa_assets

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

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8080, workers=1)
