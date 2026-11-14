"""Authenticated M1 campaign and Studio orchestration HTTP API."""
from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from typing import Annotated, Any, Literal

import boto3
import uvicorn
from fastapi import Body, Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from temporalio.client import Client
from temporalio.exceptions import TemporalError, WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from assets import QAAssetError, S3QAAssets
from auth import AuthenticationError, Claims, OIDCAuthenticator, scope_claims
from money import MoneyServiceError, MoneyServices
from people import ROLES, CampaignsService, CreatorsService, PeopleService, PeopleServiceError
from workflows import TemporalWorkflows

ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
ResourceId = Annotated[str, Path(pattern=ID_PATTERN)]
# Creators Home ids and bodies (svc-creators validates the bodies themselves).
CreatorId = Annotated[str, Path(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")]
CreatorStage = Annotated[str, Path(pattern=r"^[a-z_]{2,32}$")]
CreatorBody = Annotated[dict[str, Any], Body()]


# Invisible characters that have no place in names, ids or free text: C0/C1 controls (except tab and line breaks),
# DEL, and the bidirectional overrides that make text display differently from what it says.
_HIDDEN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")


def _walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_strings(item)
    elif isinstance(value, BaseModel):
        yield from _walk_strings(dict(value))


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="after")
    def no_hidden_characters(self):
        for text in _walk_strings(dict(self)):
            if _HIDDEN.search(text):
                raise ValueError("text contains control or direction-override characters")
        return self


class CampaignStart(InputModel):
    campaign_id: str = Field(pattern=ID_PATTERN)
    cycle_id: str = Field(pattern=ID_PATTERN)
    brief: str = Field(min_length=1, max_length=10_000)
    amount_minor: StrictInt = Field(gt=0, le=1_000_000_000_000)
    currency: Literal["NGN", "USD"] = "NGN"


class ContactRow(InputModel):
    first_name: str = Field(default="", max_length=80)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=20)
    email_consent: bool = False
    whatsapp_consent: bool = False


class ContactImport(InputModel):
    contacts: list[ContactRow] = Field(min_length=1, max_length=5000)


class SocialConnect(InputModel):
    api_key: str = Field(min_length=10, max_length=500, pattern=r"^[A-Za-z0-9._~+/=-]+$")


class CampaignApproval(InputModel):
    approval_id: str = Field(min_length=1, max_length=128)


class StudioStart(InputModel):
    pack_id: str = Field(pattern=ID_PATTERN)
    product_id: str = Field(pattern=ID_PATTERN)
    tier: Literal["starter", "growth", "pro"]
    source_image: str = Field(min_length=1, max_length=1_024)
    # Which image model: the deployment's reviewed default unless an operator names another.
    checkpoint: str = Field(default_factory=lambda: os.environ.get("STUDIO_CHECKPOINT", "v1-5-pruned-emaonly.safetensors"),
                            min_length=1, max_length=255, pattern=r"^[A-Za-z0-9._-]+\.safetensors$")
    materials: str = Field(default="", max_length=500)
    dimensions: str = Field(default="", max_length=200)
    price_text: str = Field(default="", max_length=100)
    style: str = Field(default="", max_length=300)


class StudioReview(InputModel):
    decision: Literal["approve", "reject"]
    reason: str = Field(default="", max_length=1_000)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)


class OfferStart(InputModel):
    job_id: str = Field(pattern=ID_PATTERN)
    offer: Literal["03", "05", "07", "08"]
    amount_minor: StrictInt = Field(gt=0, le=1_000_000_000_000)
    currency: Literal["NGN", "USD"] = "NGN"
    payload: dict[str, Any]

    @model_validator(mode="after")
    def valid_budget(self):
        for field in ("cost_estimate_minor", "job_ceiling_minor"):
            value = self.payload.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field} must be positive exact minor units")
        if self.payload["job_ceiling_minor"] < self.payload["cost_estimate_minor"]:
            raise ValueError("job_ceiling_minor must be at least cost_estimate_minor")
        return self


class OfferReview(InputModel):
    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=1, max_length=1_000)
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)


class EngagementStart(InputModel):
    engagement_id: str = Field(pattern=ID_PATTERN)
    offer: Literal["27", "28", "29", "30", "31", "32", "33", "34"]
    title: str = Field(min_length=1, max_length=160)
    scope: str = Field(min_length=1, max_length=10_000)
    requested_outcome: str = Field(default="", max_length=5_000)


class EngagementReview(InputModel):
    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=1, max_length=1_000)
    plan_sha256: str = Field(pattern=SHA256_PATTERN)


class PartyInput(InputModel):
    legal_name: str = Field(min_length=2, max_length=160)
    tin: str = Field(pattern=r"^\d{8,14}$")
    address: str = Field(min_length=2, max_length=300)


class BillingCycleStart(InputModel):
    subscription_id: str = Field(pattern=ID_PATTERN)
    offer: Literal["01", "02", "03", "04", "05", "06", "07", "08"]
    cycle: str = Field(pattern=r"^\d{4}-(?:0[1-9]|1[0-2])$")
    issue_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    retainer_minor: StrictInt = Field(gt=0, le=1_000_000_000_000)
    setup_minor: StrictInt = Field(default=0, ge=0, le=1_000_000_000_000)
    usage_minor: StrictInt = Field(default=0, ge=0, le=1_000_000_000_000)
    tax_minor: StrictInt = Field(default=0, ge=0, le=1_000_000_000_000)
    currency: Literal["NGN", "USD"] = "NGN"
    seller: PartyInput
    buyer: PartyInput
    billing_whatsapp: str = Field(pattern=r"^\+[1-9]\d{7,14}$")


class TopupStart(InputModel):
    amount_minor: StrictInt = Field(gt=0, le=1_000_000_000_000)
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
    basis_points: StrictInt = Field(ge=0, le=10_000)
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
    if app.state.people is None and app.state.configure_money:
        app.state.people = PeopleService.from_env()
    if app.state.campaigns is None and app.state.configure_money:
        app.state.campaigns = CampaignsService.from_env()
    if app.state.creators is None and app.state.configure_money:
        app.state.creators = CreatorsService.from_env()
    yield


def error(message: str, status: int, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


async def current_claims(request: Request) -> Claims:
    try:
        claims = await request.app.state.authenticator.authenticate(request.headers.get("Authorization"))
        return scope_claims(claims, request.headers.get("X-VolumeOS-Tenant"))
    except AuthenticationError as exc:
        raise error("Valid OIDC credentials and tenant scope are required.", 401, "unauthorized") from exc


def need(*roles: str):
    """Authenticate and check the role as a dependency. FastAPI resolves dependencies before it validates the body,
    so a caller without the role is refused (403) before any 400 can describe the request format."""
    async def dependency(request: Request) -> Claims:
        claims = await current_claims(request)
        if not any(claims.can(role) for role in roles):
            needed = " or ".join(roles)
            raise error(f"Required role: {needed}.", 403, "forbidden")
        return claims
    return dependency


def require(claims: Claims, role: str) -> None:
    if not claims.can(role):
        raise error(f"Required role: {role}.", 403, "forbidden")


def verify_snapshot(snapshot: dict[str, Any], claims: Claims, id_field: str, expected_id: str) -> None:
    if snapshot.get("tenant_id") != claims.tenant_id or snapshot.get(id_field) != expected_id:
        # Never expose whether another tenant's workflow exists.
        raise error("Workflow was not found in this tenant.", 404, "not_found")


def sorted_snapshots(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(snapshots, key=lambda item: str(item.get("updated_at", "")), reverse=True)


class PersonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    role: Literal[ROLES]  # type: ignore[valid-type]
    # Shown in the sign-in email; svc-provisioning drops anything that isn't a plain one-line name.
    business_name: str | None = Field(default=None, max_length=80)


class PersonUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal[ROLES] | None = None  # type: ignore[valid-type]
    enabled: bool | None = None
    # Emails a fresh one-time password to someone who hasn't signed in yet (a lost sign-in email).
    send_sign_in: bool | None = None
    business_name: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def something_changes(self) -> "PersonUpdate":
        if self.send_sign_in:
            if self.role is not None or self.enabled is not None:
                raise ValueError("send the sign-in email on its own")
            return self
        if self.role is None and self.enabled is None:
            raise ValueError("role or enabled is required")
        return self


def create_app(workflows: Any = None, authenticator: Any = None, qa_assets: Any = None, money: Any = None,
               people: Any = None, campaigns: Any = None, creators: Any = None) -> FastAPI:
    app = FastAPI(title="VolumeOS Orchestrator", version="0.5.0", lifespan=lifespan)
    app.state.workflows = workflows
    app.state.authenticator = authenticator
    app.state.qa_assets = qa_assets
    app.state.money = money
    app.state.people = people
    app.state.campaigns = campaigns
    app.state.creators = creators
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
    async def invalid_request(_request: Request, exc: RequestValidationError):
        fields = sorted({".".join(str(part) for part in item.get("loc", ())[1:]) or "body" for item in exc.errors()})
        message = f"Check these fields: {', '.join(fields)}." if fields else "Request fields are invalid."
        return JSONResponse({"error": {"code": "invalid_request", "message": message, "fields": fields}}, status_code=400)

    @app.exception_handler(RPCError)
    async def workflow_rpc_error(_request: Request, exc: RPCError):
        if exc.status == RPCStatusCode.NOT_FOUND:
            return JSONResponse({"error": {"code": "not_found", "message": "Workflow was not found in this tenant."}}, status_code=404)
        return JSONResponse({"error": {"code": "orchestrator_unavailable", "message": "Workflow service is unavailable."}}, status_code=503)

    @app.exception_handler(TemporalError)
    async def temporal_unavailable(_request: Request, _exc: TemporalError):
        return JSONResponse({"error": {"code": "orchestrator_unavailable", "message": "Workflow service is unavailable."}}, status_code=503)

    @app.exception_handler(PeopleServiceError)
    async def people_error(_request: Request, exc: PeopleServiceError):
        return JSONResponse({"error": {"code": exc.code, "message": str(exc)}}, status_code=exc.status)

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

    def people_service() -> Any:
        if app.state.people is None:
            raise error("Managing people isn't set up on this deployment.", 503, "people_not_configured")
        return app.state.people

    def campaigns_service() -> Any:
        if app.state.campaigns is None:
            raise error("Campaigns aren't set up on this deployment.", 503, "campaigns_not_configured")
        return app.state.campaigns

    def check_grant(claims: Claims, role: str | None) -> None:
        # A business's own super operator runs its team; only the agency's platform operators create super operators.
        if role == "super-operator" and not claims.platform:
            raise error("Only platform operators can give someone the super operator role.", 403, "forbidden")

    @app.get("/v1/people")
    async def people(claims: Claims = Depends(need("super-operator"))):
        return await people_service().list(claims.tenant_id)

    @app.post("/v1/people", status_code=201)
    async def add_person(body: PersonCreate, claims: Claims = Depends(need("super-operator"))):
        check_grant(claims, body.role)
        return await people_service().create(claims.tenant_id, body.model_dump(exclude_none=True))

    @app.patch("/v1/people/{person_id}")
    async def update_person(person_id: Annotated[str, Path(pattern=r"^[a-f0-9-]{36}$")], body: PersonUpdate,
                            claims: Claims = Depends(need("super-operator"))):
        check_grant(claims, body.role)
        if person_id == claims.actor:
            raise error("You can't change your own role or access.", 409, "own_access")
        return await people_service().update(claims.tenant_id, person_id, body.model_dump(exclude_none=True))

    @app.get("/v1/campaigns")
    async def campaigns(claims: Claims = Depends(need("reader"))):
        snapshots = await app.state.workflows.list(f"campaign:{claims.tenant_id}:")
        for snapshot in snapshots:
            if snapshot.get("tenant_id") != claims.tenant_id:
                raise error("Workflow tenant scope mismatch.", 403, "forbidden")
        return {"campaigns": sorted_snapshots(snapshots)}

    @app.post("/v1/campaigns", status_code=202)
    async def start_campaign(body: CampaignStart, claims: Claims = Depends(need("delivery"))):
        argument = body.model_dump() | {"tenant_id": claims.tenant_id}
        try:
            await app.state.workflows.start(
                "CampaignWorkflow", argument, f"campaign:{claims.tenant_id}:{body.campaign_id}", "campaigns-v1",
            )
        except WorkflowAlreadyStartedError as exc:
            raise error("Campaign ID already exists.", 409, "conflict") from exc
        return {"status": "accepted", "campaign_id": body.campaign_id}

    @app.post("/v1/campaigns/{campaign_id}/approval", status_code=202)
    async def approve_campaign(campaign_id: ResourceId, body: CampaignApproval, claims: Claims = Depends(need("delivery"))):
        workflow_id = f"campaign:{claims.tenant_id}:{campaign_id}"
        snapshot = await app.state.workflows.query(workflow_id)
        verify_snapshot(snapshot, claims, "campaign_id", campaign_id)
        if snapshot.get("status") != "awaiting_approval":
            raise error("Campaign is not awaiting human approval.", 409, "invalid_state")
        await app.state.workflows.signal(workflow_id, "approve", {
            "approval_id": body.approval_id, "approver": claims.actor,
        })
        return {"status": "accepted", "campaign_id": campaign_id}

    @app.get("/v1/campaigns/{campaign_id}/detail")
    async def campaign_detail(campaign_id: ResourceId, claims: Claims = Depends(need("reader"))):
        """The AI draft a person approves, and who it reached per channel once sent."""
        return await campaigns_service().campaign(claims.tenant_id, campaign_id)

    # Contacts are customers' personal data: only the roles that run campaigns see or change them.
    @app.get("/v1/contacts")
    async def contacts(limit: Annotated[int, Query(ge=1, le=500)] = 50, offset: Annotated[int, Query(ge=0)] = 0,
                       claims: Claims = Depends(need("delivery"))):
        return await campaigns_service().contacts(claims.tenant_id, limit, offset)

    @app.post("/v1/contacts/import")
    async def import_contacts(body: ContactImport, claims: Claims = Depends(need("delivery"))):
        return await campaigns_service().import_contacts(claims.tenant_id, body.model_dump())

    @app.delete("/v1/contacts/{contact_id}")
    async def delete_contact(contact_id: Annotated[str, Path(pattern=r"^[a-f0-9-]{36}$")], claims: Claims = Depends(need("delivery"))):
        return await campaigns_service().delete_contact(claims.tenant_id, contact_id)

    @app.get("/v1/social")
    async def social(claims: Claims = Depends(need("reader"))):
        """The business's connected social accounts (Postiz), for Content Cadence."""
        return await campaigns_service().social(claims.tenant_id)

    @app.put("/v1/social")
    async def connect_social(body: SocialConnect, claims: Claims = Depends(need("delivery"))):
        return await campaigns_service().connect_social(claims.tenant_id, body.api_key)

    @app.delete("/v1/social")
    async def disconnect_social(claims: Claims = Depends(need("delivery"))):
        return await campaigns_service().disconnect_social(claims.tenant_id)

    # Creators Home (Offer 35) ------------------------------------------------------------------------------------
    # svc-creators validates every body and keeps each business's data apart; here the person's role is checked and
    # who they are is passed on for approvals and the project history. Reading needs any role; changing needs delivery.
    def creators_service() -> Any:
        if app.state.creators is None:
            raise error("Creators Home isn't set up on this deployment.", 503, "creators_not_configured")
        return app.state.creators

    @app.get("/v1/creators/stages")
    async def creator_stages(claims: Claims = Depends(need("reader"))):
        return await creators_service().call("GET", "/stages", claims.tenant_id, claims.actor)

    @app.get("/v1/creators/channels")
    async def creator_channels(claims: Claims = Depends(need("reader"))):
        return await creators_service().call("GET", "/channels", claims.tenant_id, claims.actor)

    @app.post("/v1/creators/channels", status_code=201)
    async def create_creator_channel(body: CreatorBody, claims: Claims = Depends(need("delivery"))):
        return await creators_service().call("POST", "/channels", claims.tenant_id, claims.actor, body)

    @app.patch("/v1/creators/channels/{channel_id}")
    async def update_creator_channel(channel_id: CreatorId, body: CreatorBody, claims: Claims = Depends(need("delivery"))):
        return await creators_service().call("PATCH", f"/channels/{channel_id}", claims.tenant_id, claims.actor, body)

    @app.delete("/v1/creators/channels/{channel_id}")
    async def delete_creator_channel(channel_id: CreatorId, claims: Claims = Depends(need("delivery"))):
        return await creators_service().call("DELETE", f"/channels/{channel_id}", claims.tenant_id, claims.actor)

    @app.get("/v1/creators/projects")
    async def creator_projects(channel_id: Annotated[str | None, Query(pattern=r"^[0-9a-f-]{36}$")] = None,
                               claims: Claims = Depends(need("reader"))):
        params = {"channel_id": channel_id} if channel_id else None
        return await creators_service().call("GET", "/projects", claims.tenant_id, claims.actor, params=params)

    @app.post("/v1/creators/projects", status_code=201)
    async def create_creator_project(body: CreatorBody, claims: Claims = Depends(need("delivery"))):
        return await creators_service().call("POST", "/projects", claims.tenant_id, claims.actor, body)

    @app.get("/v1/creators/projects/{project_id}")
    async def creator_project(project_id: CreatorId, claims: Claims = Depends(need("reader"))):
        return await creators_service().call("GET", f"/projects/{project_id}", claims.tenant_id, claims.actor)

    @app.get("/v1/creators/projects/{project_id}/launch")
    async def creator_launch(project_id: CreatorId, claims: Claims = Depends(need("reader"))):
        """Title score, launch checklist, upload times and (with YouTube connected) the channel's real baseline."""
        return await creators_service().call("GET", f"/projects/{project_id}/launch", claims.tenant_id, claims.actor)

    @app.patch("/v1/creators/projects/{project_id}")
    async def update_creator_project(project_id: CreatorId, body: CreatorBody, claims: Claims = Depends(need("delivery"))):
        return await creators_service().call("PATCH", f"/projects/{project_id}", claims.tenant_id, claims.actor, body)

    @app.put("/v1/creators/projects/{project_id}/{section}")
    async def set_creator_section(project_id: CreatorId, section: Literal["title", "script", "thumbnail"], body: CreatorBody,
                                  claims: Claims = Depends(need("delivery"))):
        return await creators_service().call("PUT", f"/projects/{project_id}/{section}", claims.tenant_id, claims.actor, body)

    @app.post("/v1/creators/projects/{project_id}/stages/{stage}/{action}")
    async def creator_stage_action(project_id: CreatorId, stage: CreatorStage, action: Literal["approve", "reopen"],
                                   body: CreatorBody, claims: Claims = Depends(need("delivery"))):
        return await creators_service().call("POST", f"/projects/{project_id}/stages/{stage}/{action}",
                                             claims.tenant_id, claims.actor, body)

    @app.post("/v1/creators/projects/{project_id}/jobs", status_code=202)
    async def start_creator_job(project_id: CreatorId, body: CreatorBody, claims: Claims = Depends(need("delivery"))):
        """Writing the script or voicing the approved script: started by a person, named in the history."""
        return await creators_service().call("POST", f"/projects/{project_id}/jobs", claims.tenant_id, claims.actor, body)

    @app.get("/v1/creators/channels/{channel_id}/youtube")
    async def creator_youtube(channel_id: CreatorId, claims: Claims = Depends(need("reader"))):
        """Whether the channel is linked to a YouTube channel (never the Google tokens)."""
        return await creators_service().call("GET", f"/channels/{channel_id}/youtube", claims.tenant_id, claims.actor)

    @app.post("/v1/creators/channels/{channel_id}/youtube/connect")
    async def connect_creator_youtube(channel_id: CreatorId, claims: Claims = Depends(need("delivery"))):
        """The Google sign-in address; Google sends the person back to svc-creators' public callback."""
        return await creators_service().call("POST", f"/channels/{channel_id}/youtube/connect", claims.tenant_id, claims.actor)

    @app.delete("/v1/creators/channels/{channel_id}/youtube")
    async def disconnect_creator_youtube(channel_id: CreatorId, claims: Claims = Depends(need("delivery"))):
        return await creators_service().call("DELETE", f"/channels/{channel_id}/youtube", claims.tenant_id, claims.actor)

    @app.get("/v1/creators/projects/{project_id}/jobs")
    async def creator_jobs(project_id: CreatorId, claims: Claims = Depends(need("reader"))):
        return await creators_service().call("GET", f"/projects/{project_id}/jobs", claims.tenant_id, claims.actor)

    @app.get("/v1/creators/voices")
    async def creator_voices(claims: Claims = Depends(need("reader"))):
        return await creators_service().call("GET", "/voices", claims.tenant_id, claims.actor)

    @app.post("/v1/creators/voices/preview")
    async def creator_voice_preview(body: CreatorBody, claims: Claims = Depends(need("delivery"))):
        data, media, _ = await creators_service().raw("POST", "/voices/preview", claims.tenant_id, claims.actor, body)
        return Response(data, media_type=media)

    @app.get("/v1/creators/projects/{project_id}/audio/{name}")
    async def creator_audio(project_id: CreatorId, name: Annotated[str, Path(pattern=r"^(narration|chapter-\d{2})$")],
                            claims: Claims = Depends(need("reader"))):
        data, media, disposition = await creators_service().raw("GET", f"/projects/{project_id}/audio/{name}", claims.tenant_id, claims.actor)
        return Response(data, media_type=media, headers={"Content-Disposition": disposition} if disposition else None)

    @app.put("/v1/creators/projects/{project_id}/scenes/{number}")
    async def edit_creator_scene(project_id: CreatorId, number: Annotated[int, Path(ge=1, le=999)], body: CreatorBody,
                                 claims: Claims = Depends(need("delivery"))):
        return await creators_service().call("PUT", f"/projects/{project_id}/scenes/{number}", claims.tenant_id, claims.actor, body)

    @app.get("/v1/creators/projects/{project_id}/images/{name}")
    async def creator_image(project_id: CreatorId, name: Annotated[str, Path(pattern=r"^(scene-\d{3}|thumb-\d)$")],
                            claims: Claims = Depends(need("reader"))):
        data, media, _ = await creators_service().raw("GET", f"/projects/{project_id}/images/{name}", claims.tenant_id, claims.actor)
        return Response(data, media_type=media)

    @app.get("/v1/creators/projects/{project_id}/scene-images.zip")
    async def creator_images_zip(project_id: CreatorId, claims: Claims = Depends(need("reader"))):
        data, media, disposition = await creators_service().raw("GET", f"/projects/{project_id}/images.zip", claims.tenant_id, claims.actor)
        return Response(data, media_type=media, headers={"Content-Disposition": disposition} if disposition else None)

    @app.get("/v1/creators/projects/{project_id}/video/{name}")
    async def creator_video(project_id: CreatorId, name: Literal["video.mp4", "captions.srt"], claims: Claims = Depends(need("reader"))):
        body, headers = await creators_service().stream(f"/projects/{project_id}/video/{name}", claims.tenant_id, claims.actor)
        return StreamingResponse(body, media_type=headers.pop("content-type", "application/octet-stream"), headers=headers)

    @app.get("/v1/creators/projects/{project_id}/motion/{name}")
    async def creator_motion(project_id: CreatorId, name: Annotated[str, Path(pattern=r"^scene-\d{3}\.mp4$")],
                             claims: Claims = Depends(need("reader"))):
        """One scene's motion clip, streamed."""
        body, headers = await creators_service().stream(f"/projects/{project_id}/motion/{name}", claims.tenant_id, claims.actor)
        return StreamingResponse(body, media_type=headers.pop("content-type", "video/mp4"), headers=headers)

    @app.get("/v1/creators/projects/{project_id}/events")
    async def creator_events(project_id: CreatorId, claims: Claims = Depends(need("reader"))):
        return await creators_service().call("GET", f"/projects/{project_id}/events", claims.tenant_id, claims.actor)

    @app.get("/v1/studio/packs")
    async def studio_packs(claims: Claims = Depends(need("reader"))):
        snapshots = await app.state.workflows.list(f"studio:{claims.tenant_id}:")
        for snapshot in snapshots:
            if snapshot.get("tenant_id") != claims.tenant_id:
                raise error("Workflow tenant scope mismatch.", 403, "forbidden")
        return {"packs": sorted_snapshots(snapshots)}

    @app.post("/v1/studio/uploads", status_code=201)
    async def upload_studio_photo(request: Request, claims: Claims = Depends(need("delivery"))):
        """The product photo a Studio pack starts from (raw PNG/JPEG/WebP body), stored under this business."""
        data = await request.body()
        try:
            key = await app.state.qa_assets.put_upload(claims.tenant_id, data)
        except QAAssetError as exc:
            raise error(str(exc), 400, "invalid_upload") from exc
        return {"source_image": key}

    @app.post("/v1/studio/packs", status_code=202)
    async def start_studio_pack(body: StudioStart, claims: Claims = Depends(need("delivery"))):
        source = PurePosixPath(body.source_image)
        if (not body.source_image.startswith(f"{claims.tenant_id}/uploads/") or source.is_absolute()
                or ".." in source.parts):
            raise error("Upload the product photo first; use the key the upload returned.", 400, "invalid_source")
        argument = body.model_dump() | {"tenant_id": claims.tenant_id}
        try:
            await app.state.workflows.start(
                "StudioWorkflow", argument, f"studio:{claims.tenant_id}:{body.pack_id}", "studio-v1",
            )
        except WorkflowAlreadyStartedError as exc:
            raise error("Studio pack ID already exists.", 409, "conflict") from exc
        return {"status": "accepted", "pack_id": body.pack_id}

    @app.get("/v1/studio/packs/{pack_id}/assets")
    async def studio_assets(pack_id: ResourceId, claims: Claims = Depends(need("reader"))):
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

    @app.get("/v1/studio/packs/{pack_id}/assets/{name}")
    async def studio_asset_image(pack_id: ResourceId, name: Annotated[str, Path(pattern=r"^asset-\d{2}\.png$")],
                                 claims: Claims = Depends(need("reader"))):
        snapshot = await app.state.workflows.query(f"studio:{claims.tenant_id}:{pack_id}")
        verify_snapshot(snapshot, claims, "pack_id", pack_id)
        if snapshot.get("status") != "awaiting_qa":
            raise error("Studio pack is not awaiting human QA.", 409, "invalid_state")
        try:
            data = await app.state.qa_assets.read_qa_image(claims.tenant_id, pack_id, snapshot.get("manifest_sha256", ""),
                                                           snapshot.get("assets", 0), name)
        except QAAssetError as exc:
            raise error(str(exc), 404, "not_found") from exc
        return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/v1/studio/packs/{pack_id}/delivered")
    async def studio_delivered(pack_id: ResourceId, claims: Claims = Depends(need("reader"))):
        """The approved pack's files: WhatsApp (1080 px) and Jumia (1000 px, white background) JPEGs."""
        snapshot = await app.state.workflows.query(f"studio:{claims.tenant_id}:{pack_id}")
        verify_snapshot(snapshot, claims, "pack_id", pack_id)
        if snapshot.get("status") != "delivered":
            raise error("This pack hasn't been delivered yet.", 409, "invalid_state")
        try:
            files = await app.state.qa_assets.delivered(claims.tenant_id, pack_id)
        except QAAssetError as exc:
            raise error(str(exc), 409, "invalid_delivery") from exc
        return {"pack_id": pack_id, "files": [{"path": f, "url": f"/v1/studio/packs/{pack_id}/delivered/{f}"} for f in files]}

    @app.get("/v1/studio/packs/{pack_id}/delivered/{channel}/{name}")
    async def studio_delivered_file(pack_id: ResourceId, channel: Literal["whatsapp", "jumia"],
                                    name: Annotated[str, Path(pattern=r"^asset-\d{2}\.jpg$")], claims: Claims = Depends(need("reader"))):
        snapshot = await app.state.workflows.query(f"studio:{claims.tenant_id}:{pack_id}")
        verify_snapshot(snapshot, claims, "pack_id", pack_id)
        if snapshot.get("status") != "delivered":
            raise error("This pack hasn't been delivered yet.", 409, "invalid_state")
        try:
            data = await app.state.qa_assets.read_delivered(claims.tenant_id, pack_id, f"{channel}/{name}")
        except QAAssetError as exc:
            raise error(str(exc), 404, "not_found") from exc
        return Response(content=data, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{pack_id}-{channel}-{name}"'})

    @app.post("/v1/studio/packs/{pack_id}/review", status_code=202)
    async def review_studio_pack(pack_id: ResourceId, body: StudioReview, claims: Claims = Depends(need("delivery"))):
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
    async def offer_jobs(claims: Claims = Depends(need("reader"))):
        snapshots = await app.state.workflows.list(f"offer:{claims.tenant_id}:")
        for snapshot in snapshots:
            if snapshot.get("tenant_id") != claims.tenant_id:
                raise error("Workflow tenant scope mismatch.", 403, "forbidden")
        return {"jobs": sorted_snapshots(snapshots)}

    @app.post("/v1/offers/jobs", status_code=202)
    async def start_offer_job(body: OfferStart, claims: Claims = Depends(need("delivery"))):
        argument = body.model_dump() | {"tenant_id": claims.tenant_id}
        try:
            await app.state.workflows.start(
                "OfferWorkflow", argument, f"offer:{claims.tenant_id}:{body.job_id}", "offers-v1",
            )
        except WorkflowAlreadyStartedError as exc:
            raise error("Offer job ID already exists.", 409, "conflict") from exc
        return {"status": "accepted", "job_id": body.job_id}

    @app.get("/v1/offers/jobs/{job_id}")
    async def offer_job(job_id: ResourceId, claims: Claims = Depends(need("reader"))):
        snapshot = await app.state.workflows.query(f"offer:{claims.tenant_id}:{job_id}")
        verify_snapshot(snapshot, claims, "job_id", job_id)
        return snapshot

    @app.post("/v1/offers/jobs/{job_id}/review", status_code=202)
    async def review_offer_job(job_id: ResourceId, body: OfferReview, claims: Claims = Depends(need("delivery", "analyst"))):
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

    @app.get("/v1/engagements")
    async def engagements(claims: Claims = Depends(need("reader"))):
        snapshots = await app.state.workflows.list(f"engagement:{claims.tenant_id}:")
        for snapshot in snapshots:
            if snapshot.get("tenant_id") != claims.tenant_id:
                raise error("Workflow tenant scope mismatch.", 403, "forbidden")
        return {"engagements": sorted_snapshots(snapshots)}

    @app.post("/v1/engagements", status_code=202)
    async def start_engagement(body: EngagementStart, claims: Claims = Depends(need("delivery"))):
        argument = body.model_dump() | {"tenant_id": claims.tenant_id}
        try:
            await app.state.workflows.start(
                "EngagementWorkflow", argument,
                f"engagement:{claims.tenant_id}:{body.engagement_id}", "engagements-v1",
            )
        except WorkflowAlreadyStartedError as exc:
            raise error("Engagement ID already exists.", 409, "conflict") from exc
        return {
            "status": "accepted", "engagement_id": body.engagement_id,
            "commercial_status": "source-only-unpriced", "delivery_mode": "plan-only",
        }

    @app.get("/v1/engagements/{engagement_id}")
    async def engagement(engagement_id: ResourceId, claims: Claims = Depends(need("reader"))):
        snapshot = await app.state.workflows.query(f"engagement:{claims.tenant_id}:{engagement_id}")
        verify_snapshot(snapshot, claims, "engagement_id", engagement_id)
        return snapshot

    @app.post("/v1/engagements/{engagement_id}/review", status_code=202)
    async def review_engagement(
        engagement_id: ResourceId, body: EngagementReview, claims: Claims = Depends(need("delivery", "analyst")),
    ):
        workflow_id = f"engagement:{claims.tenant_id}:{engagement_id}"
        snapshot = await app.state.workflows.query(workflow_id)
        verify_snapshot(snapshot, claims, "engagement_id", engagement_id)
        plan = snapshot.get("plan")
        if snapshot.get("status") != "awaiting-approval" or not isinstance(plan, dict):
            raise error("Engagement is not awaiting plan approval.", 409, "invalid_state")
        if plan.get("sha256") != body.plan_sha256:
            raise error("Plan changed; reload the engagement before reviewing.", 409, "stale_plan")
        await app.state.workflows.signal(workflow_id, "review", {
            "plan_sha256": body.plan_sha256, "reviewer": claims.actor,
            "decision": body.decision, "reason": body.reason,
            "reviewed_at_unix": int(datetime.now(timezone.utc).timestamp()),
        })
        return {"status": "accepted", "engagement_id": engagement_id}

    @app.get("/v1/billing/balance/{currency}")
    async def billing_balance(currency: Literal["NGN", "USD"], claims: Claims = Depends(need("reader"))):
        return await money_services().balance(claims.tenant_id, currency)

    @app.post("/v1/billing/topups/{provider}/{reference}", status_code=201)
    async def start_topup(provider: Literal["paystack", "flutterwave", "moniepoint"], reference: ResourceId,
                          body: TopupStart, request: Request, claims: Claims = Depends(need("client-owner", "delivery"))):
        allowed = {origin.strip().rstrip("/") for origin in os.environ.get("TOPUP_REDIRECT_ORIGINS", "").split(",") if origin.strip()}
        allowed = allowed or {f"https://{request.headers.get('host', '')}"}
        target = urlsplit(body.redirect_url)
        if f"{target.scheme}://{target.netloc}" not in allowed:
            raise error("The return address must be on this site.", 400, "invalid_request")
        payload = body.model_dump() | {"amount_minor": str(body.amount_minor)}
        return await money_services().topup(claims.tenant_id, provider, reference, payload)

    @app.get("/v1/billing/cycles")
    async def billing_cycles(claims: Claims = Depends(need("reader"))):
        snapshots = await app.state.workflows.list(f"billing:{claims.tenant_id}:")
        for snapshot in snapshots:
            if snapshot.get("tenant_id") != claims.tenant_id:
                raise error("Workflow tenant scope mismatch.", 403, "forbidden")
        return {"cycles": sorted_snapshots(snapshots)}

    @app.post("/v1/billing/cycles", status_code=202)
    async def start_billing_cycle(body: BillingCycleStart, claims: Claims = Depends(need("sales"))):
        argument = body.model_dump() | {
            "tenant_id": claims.tenant_id,
            # Pause/restore always reads a fresh optimistic tenancy version.
            "tenant_version": 1,
            "dunning_delays_seconds": [3 * 86400, 7 * 86400, 14 * 86400],
        }
        workflow_id = f"billing:{claims.tenant_id}:{body.subscription_id}:{body.cycle}"
        try:
            # A cycle that failed (e.g. on a fault since fixed) can be run again: its invoice number, reservation and
            # posting are keyed by tenant/subscription/cycle, so the rerun reuses them instead of charging twice.
            await app.state.workflows.start("BillingCycleWorkflow", argument, workflow_id, "billing-v1", retry_failed=True)
        except WorkflowAlreadyStartedError as exc:
            raise error("Billing cycle already exists.", 409, "conflict") from exc
        return {"status": "accepted", "subscription_id": body.subscription_id, "cycle": body.cycle}

    @app.get("/v1/billing/documents")
    async def billing_documents(claims: Claims = Depends(need("reader"))):
        return await money_services().documents(claims.tenant_id)

    @app.get("/v1/billing/documents/{kind}/{document_id}/pdf")
    async def billing_document_pdf(kind: Literal["invoice", "receipt"], document_id: ResourceId,
                                   claims: Claims = Depends(need("reader"))):
        content, number = await money_services().document_pdf(claims.tenant_id, kind, document_id)
        return Response(
            content=content, media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{number}.pdf"'},
        )

    @app.get("/v1/billing/reports/ar-aging")
    async def ar_aging_report(as_of: date | None = None, claims: Claims = Depends(need("reader"))):
        report_date = as_of or datetime.now(timezone.utc).date()
        return await money_services().ar_aging(claims.tenant_id, report_date.isoformat())

    @app.post("/v1/billing/reports/ar-aging/exports")
    async def export_ar_aging_report(body: ArExport, claims: Claims = Depends(need("finance"))):
        return await money_services().export_ar_aging(claims.tenant_id, body.as_of.isoformat())

    @app.get("/v1/billing/reports/partner-share/{partner_id}")
    async def partner_share_report(partner_id: ResourceId, start: date, end: date, claims: Claims = Depends(need("reader"))):
        if end < start or (end - start).days > 3660:
            raise error("Statement period must be valid and no longer than ten years.", 400, "invalid_request")
        return await money_services().partner_statement(claims.tenant_id, partner_id, start.isoformat(), end.isoformat())

    @app.post("/v1/billing/reports/partner-share/{partner_id}/exports")
    async def export_partner_share_report(partner_id: ResourceId, body: PartnerExport, claims: Claims = Depends(need("finance"))):
        return await money_services().export_partner_statement(
            claims.tenant_id, partner_id, body.start.isoformat(), body.end.isoformat(),
        )

    @app.get("/v1/billing/reports/trial-balance/{currency}")
    async def trial_balance_report(currency: Literal["NGN", "USD"], claims: Claims = Depends(need("finance"))):
        if claims.tenant_id != "platform":
            raise error("Platform finance scope is required.", 403, "forbidden")
        return await money_services().trial_balance(currency)

    @app.post("/v1/billing/reports/trial-balance/{currency}/exports")
    async def export_trial_balance_report(currency: Literal["NGN", "USD"], claims: Claims = Depends(need("finance"))):
        if claims.tenant_id != "platform":
            raise error("Platform finance scope is required.", 403, "forbidden")
        return await money_services().export_trial_balance(currency)

    @app.post("/v1/billing/partner-contracts", status_code=201)
    async def create_partner_contract(body: PartnerContractStart, claims: Claims = Depends(need("finance"))):
        return await money_services().create_partner_contract(claims.tenant_id, body.model_dump(mode="json"))

    @app.get("/v1/billing/partner-contracts")
    async def partner_contracts(partner_id: str | None = None, claims: Claims = Depends(need("reader"))):
        if partner_id is not None and not re.fullmatch(ID_PATTERN, partner_id):
            raise error("Invalid partner id.", 400, "invalid_request")
        return await money_services().partner_contracts(claims.tenant_id, partner_id)

    @app.get("/v1/billing/financial-exports/{kind}/{digest}")
    async def download_financial_export(
        kind: Literal["trial-balance", "ar-aging", "partner-share"],
        digest: Annotated[str, Path(pattern=SHA256_PATTERN)], claims: Claims = Depends(need("reader")),
    ):
        content, export_digest = await money_services().financial_export(claims.tenant_id, kind, digest)
        return Response(
            content=content, media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{kind}-{export_digest[:12]}.json"'},
        )

    @app.get("/v1/billing/collection-suspense")
    async def open_collection_suspense(claims: Claims = Depends(need("finance"))):
        if claims.tenant_id != "platform":
            raise error("Platform finance scope is required.", 403, "forbidden")
        return await money_services().open_suspense()

    @app.post("/v1/billing/collection-suspense/{provider}/{reference}/resolutions")
    async def resolve_collection_suspense(
        provider: Literal["paystack", "flutterwave", "moniepoint"], reference: ResourceId,
        body: SuspenseResolutionStart, claims: Claims = Depends(need("finance")),
    ):
        if claims.tenant_id != "platform":
            raise error("Platform finance scope is required.", 403, "forbidden")
        if body.tenant_id == "platform":
            raise error("A client tenant is required.", 400, "invalid_request")
        return await money_services().resolve_suspense(
            provider, reference, body.tenant_id, claims.actor, body.reason,
        )

    @app.post("/v1/billing/recovery", status_code=202)
    async def recover_billing(body: FundingRecovery, claims: Claims = Depends(need("delivery"))):
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
