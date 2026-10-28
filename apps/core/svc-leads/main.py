from __future__ import annotations

from dataclasses import asdict
import os
from typing import Literal
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from auth import AuthenticationError, Claims, oidc_from_env, require_roles
from leads import LeadError, LeadInput, LeadService, ModelReview
from store import PostgresConsentRegistry, PostgresLeadStore

app = FastAPI(title="VolumeOS CRM Leads", version="0.1.0")
app.state.oidc = oidc_from_env()
app.state.store = None
app.state.service = None


class HTTPGoldGateway:
    def __init__(self, base_url: str, token: str):
        if not base_url.startswith("https://") or not token:
            raise RuntimeError("GOLD_QUERY_URL and VOLUMEOS_SERVICE_TOKEN are required")
        self.base_url, self.token = base_url.rstrip("/"), token

    async def _get(self, tenant_id: str, path: str) -> dict:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(self.base_url + path, headers={"Authorization": f"Bearer {self.token}", "X-VolumeOS-Tenant": tenant_id})
            response.raise_for_status()
            document = response.json()
        if not isinstance(document, dict) or document.get("tenant_id") != tenant_id:
            raise LeadError("gold gateway returned an invalid tenant document")
        return document

    async def review(self, tenant_id, model_version, weight_version):
        path = f"/v1/gold/model-registry/{quote(model_version, safe='')}/{quote(weight_version, safe='')}"
        document = await self._get(tenant_id, path)
        return ModelReview(document["model_id"], document["model_version"], document["weight_version"], document["review_state"],
                           document["reviewer"], document["gold_lineage_id"], document["weights"])

    async def features(self, tenant_id, lead_id, h3_r9, model_version, weight_version):
        path = f"/v1/gold/lead-features/{quote(lead_id, safe='')}/{quote(h3_r9, safe='')}?model_version={quote(model_version, safe='')}&weight_version={quote(weight_version, safe='')}"
        return await self._get(tenant_id, path)


@app.on_event("startup")
async def startup():
    store = await PostgresLeadStore.connect(os.environ.get("DATABASE_URL", ""))
    app.state.store = store
    gold = HTTPGoldGateway(os.environ.get("GOLD_QUERY_URL", ""), os.environ.get("VOLUMEOS_SERVICE_TOKEN", ""))
    app.state.service = LeadService(store, PostgresConsentRegistry(store), gold=gold)


@app.on_event("shutdown")
async def shutdown():
    if app.state.store: await app.state.store.close()


@app.exception_handler(LeadError)
async def lead_error(_request, error):
    return JSONResponse(status_code=409, content={"detail": str(error)})


async def principal(request: Request) -> Claims:
    try:
        return await request.app.state.oidc.authenticate(request.headers.get("Authorization"))
    except AuthenticationError as exc:
        raise HTTPException(401, "valid OIDC credentials are required") from exc


def service(request) -> LeadService:
    if request.app.state.service is None: raise HTTPException(503, "lead service unavailable")
    return request.app.state.service


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LeadBody(Input):
    source: Literal["census", "referral", "import", "web", "partner"]
    source_record_id: str = Field(min_length=1, max_length=128)
    business_name: str = Field(min_length=2, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    contact_name: str | None = Field(default=None, max_length=200)
    address: str | None = Field(default=None, max_length=1000)
    niche: str | None = Field(default=None, max_length=1000)
    h3_r9: str | None = Field(default=None, pattern=r"^[0-9a-f]{15}$")
    consent_reference: str | None = Field(default=None, max_length=128)
    lineage_id: str | None = Field(default=None, max_length=128)


class TransitionBody(Input):
    target: Literal["contacted", "qualified", "opportunity", "recycled", "suppressed"]


class ScoreBody(Input):
    model_version: str = Field(min_length=1, max_length=128)
    weight_version: str = Field(min_length=1, max_length=128)


class CadenceBody(Input):
    step: Literal["D0", "D3", "D7", "D14", "RECYCLE"]
    approval_id: str = Field(min_length=1, max_length=128)


@app.get("/healthz")
async def health(): return {"ok": True}


@app.get("/readyz")
async def ready():
    if app.state.store is None: raise HTTPException(503, "store unavailable")
    await app.state.store.ready(); return {"ok": True}


@app.post("/v1/leads", status_code=201)
async def ingest(body: LeadBody, request: Request):
    actor = require_roles(await principal(request), "super-operator", "lead-agent")
    lead = await service(request).ingest(actor.tenant_id, LeadInput(**body.model_dump()), actor.actor)
    return asdict(lead)


@app.get("/v1/leads/{lead_id}")
async def get(lead_id: str, request: Request):
    actor = require_roles(await principal(request), "super-operator", "lead-agent", "auditor")
    return asdict(await service(request)._lead(actor.tenant_id, lead_id))


@app.post("/v1/leads/{lead_id}/transitions")
async def transition(lead_id: str, body: TransitionBody, request: Request):
    actor = require_roles(await principal(request), "super-operator", "lead-agent")
    return asdict(await service(request).transition(actor.tenant_id, lead_id, body.target, actor.actor))


@app.post("/v1/leads/{lead_id}/scores")
async def score(lead_id: str, body: ScoreBody, request: Request):
    actor = require_roles(await principal(request), "super-operator", "lead-agent")
    return asdict(await service(request).score_reviewed(actor.tenant_id, lead_id, body.model_version, body.weight_version))


@app.get("/v1/leads/{lead_id}/cadence")
async def cadence(lead_id: str, request: Request):
    actor = require_roles(await principal(request), "super-operator", "lead-agent")
    due = await service(request).due_cadence(actor.tenant_id, lead_id)
    return asdict(due) if due else {"status": "not-due"}


@app.post("/v1/leads/{lead_id}/cadence")
async def record_cadence(lead_id: str, body: CadenceBody, request: Request):
    actor = require_roles(await principal(request), "super-operator")
    return asdict(await service(request).record_cadence(actor.tenant_id, lead_id, body.step, body.approval_id, actor.actor))


@app.post("/v1/leads/{lead_id}/territory-assignment")
async def assign_territory(lead_id: str, body: ScoreBody, request: Request):
    actor = require_roles(await principal(request), "super-operator", "lead-agent")
    return asdict(await service(request).assign_territory(actor.tenant_id, lead_id, body.model_version, body.weight_version))


@app.post("/v1/leads/{lead_id}/conversion")
async def convert(lead_id: str, request: Request):
    actor = require_roles(await principal(request), "super-operator", "lead-agent")
    return asdict(await service(request).convert(actor.tenant_id, lead_id, actor.actor))
