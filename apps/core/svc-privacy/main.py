from __future__ import annotations

import os
import time
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from auth import OIDCAuthenticator, Principal, oidc_from_env, require_roles
from privacy import DSAR, PrivacyError, PrivacyService, verify_identity_proof
from redaction import RedactedAIGateway, Redactor
from store import PostgresPrivacyStore

app = FastAPI(title="VolumeOS Privacy", version="0.1.0")
app.state.oidc = oidc_from_env()
app.state.store = None
app.state.service = None


@app.on_event("startup")
async def startup() -> None:
    app.state.store = await PostgresPrivacyStore.connect(os.environ["DATABASE_URL"])
    key = os.environ.get("SUBJECT_DIGEST_KEY", "")
    if len(key.encode()) < 32:
        raise RuntimeError("SUBJECT_DIGEST_KEY must contain at least 32 bytes")
    app.state.service = PrivacyService(app.state.store, key.encode())


@app.on_event("shutdown")
async def shutdown() -> None:
    if app.state.store:
        await app.state.store.close()


@app.exception_handler(PrivacyError)
async def privacy_error(_request: Request, error: PrivacyError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(error)})


@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/readyz")
async def readyz() -> dict[str, bool]:
    if app.state.store is None:
        raise HTTPException(503, "privacy store not ready")
    await app.state.store.ready()
    return {"ok": True}


class ConsentInput(BaseModel):
    reference: str
    subject: str = Field(min_length=2, max_length=320)
    purpose: Literal["field-census", "campaign", "lead-conversion", "ai-processing", "service-delivery"]
    lawful_basis: Literal["consent", "contract", "legal-obligation", "legitimate-interest"]
    status: Literal["granted", "withdrawn"] = "granted"


class DSARInput(BaseModel):
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]*$")
    subject: str = Field(min_length=2, max_length=320)
    kind: Literal["export", "delete"]
    identity_proof: str = Field(min_length=32, max_length=1024)


class ManifestInput(BaseModel):
    object_key: str = Field(min_length=1, max_length=1024)
    files: list[dict[str, str]] = Field(min_length=1, max_length=10000)


class DeletionInput(BaseModel):
    target: Literal["postgresql", "object-store", "opensearch", "lakehouse"]
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AIInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=32000)
    model: str = Field(min_length=1, max_length=128)


async def principal(request: Request) -> Principal:
    from auth import AuthenticationError
    try:
        return await request.app.state.oidc.authenticate(request.headers.get("Authorization", ""))
    except AuthenticationError as exc:
        raise HTTPException(401, "valid OIDC credentials are required") from exc


def service(request: Request) -> PrivacyService:
    if request.app.state.service is None:
        raise HTTPException(503, "privacy service not ready")
    return request.app.state.service


@app.post("/v1/consents")
async def record_consent(body: ConsentInput, request: Request) -> dict:
    actor = require_roles(await principal(request), "super-operator", "privacy-officer")
    consent = await service(request).record_consent(actor.tenant_id, actor.actor, body.reference, body.subject,
                                                     body.purpose, body.lawful_basis, body.status)
    return {"tenant_id": consent.tenant_id, "reference": consent.reference, "subject_digest": consent.subject_digest,
            "purpose": consent.purpose, "lawful_basis": consent.lawful_basis, "status": consent.status,
            "version": consent.version}


@app.post("/v1/dsar")
async def open_dsar(body: DSARInput, request: Request) -> dict:
    actor = require_roles(await principal(request), "super-operator", "privacy-officer")
    svc = service(request)
    digest = svc.subject_digest(body.subject)
    proof_key = os.environ.get("IDENTITY_PROOF_KEY", "")
    verify_identity_proof(proof_key, body.identity_proof, digest, body.request_id, int(time.time()))
    result = await svc.open_dsar(actor.tenant_id, actor.actor, body.request_id, body.subject, body.kind, True)
    return {"tenant_id": result.tenant_id, "request_id": result.request_id, "kind": result.kind, "status": result.status}


@app.get("/v1/dsar/{request_id}")
async def get_dsar(request_id: str, request: Request) -> dict:
    actor = require_roles(await principal(request), "super-operator", "privacy-officer", "auditor", "privacy-worker")
    result = await service(request).store.dsar(actor.tenant_id, request_id)
    if result is None:
        raise HTTPException(404, "DSAR request not found")
    return {"tenant_id": result.tenant_id, "request_id": result.request_id, "kind": result.kind,
            "status": result.status, "export_manifest": result.export_manifest, "deletion": result.deletion}


@app.post("/v1/dsar/{request_id}/export")
async def attach_export(request_id: str, body: ManifestInput, request: Request) -> dict:
    actor = require_roles(await principal(request), "super-operator", "privacy-officer", "privacy-worker")
    result = await service(request).attach_export(actor.tenant_id, actor.actor, request_id, body.object_key, body.files)
    return {"request_id": result.request_id, "status": result.status}


@app.post("/v1/dsar/{request_id}/deletion")
async def acknowledge_deletion(request_id: str, body: DeletionInput, request: Request) -> dict:
    actor = require_roles(await principal(request), "super-operator", "privacy-officer", "privacy-worker")
    result = await service(request).acknowledge_deletion(actor.tenant_id, actor.actor, request_id, body.target,
                                                          body.evidence_sha256)
    return {"request_id": result.request_id, "status": result.status, "deletion": result.deletion}


@app.post("/v1/ai/redacted-completion")
async def redacted_completion(body: AIInput, request: Request) -> dict[str, Any]:
    actor = require_roles(await principal(request), "super-operator", "privacy-officer", "privacy-worker")
    endpoint = os.environ.get("AI_PROVIDER_URL", "")
    api_key = os.environ.get("AI_PROVIDER_API_KEY", "")
    async with httpx.AsyncClient(timeout=45) as client:
        gateway = RedactedAIGateway(client, endpoint, api_key, Redactor())
        return await gateway.complete(actor.tenant_id, body.prompt, body.model)
