"""Tenant/device-authorized resumable field-census HTTP API."""
from __future__ import annotations

import base64
import os
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from auth import AuthenticationError, Claims, OIDCAuthenticator
from fluvio_sink import FluvioCensusSink
from store import PostgresCensusStore
from sync import CensusError, CensusSync, InMemoryCensusStore, SHA256, Upload


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartUpload(InputModel):
    upload_id: str = Field(min_length=1, max_length=128)
    event_id: str = Field(min_length=26, max_length=26)
    total_chunks: int = Field(ge=1, le=64)
    total_bytes: int = Field(ge=1, le=8 * 1024 * 1024)
    payload_sha256: str = Field(pattern=SHA256.pattern)
    captured_at_unix: int = Field(ge=0, le=4_102_444_800)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_m: float = Field(gt=0, le=100)
    consent_reference: str = Field(min_length=1, max_length=128)
    lawful_basis: Literal["consent", "legitimate_interest", "contract", "legal_obligation"]


class PutChunk(InputModel):
    index: int = Field(ge=0, le=63)
    encoded: str = Field(min_length=4, max_length=350_000)
    expected_sha256: str = Field(pattern=SHA256.pattern)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if app.state.configure:
        app.state.store = await PostgresCensusStore.connect(os.environ.get("DATABASE_URL", ""))
        app.state.sink = FluvioCensusSink.from_env()
        app.state.authenticator = OIDCAuthenticator()
        masking_key = base64.b64decode(os.environ.get("CENSUS_MASKING_KEY", ""), validate=True)
        app.state.sync = CensusSync(app.state.store, app.state.sink, masking_key)
    yield
    if app.state.configure:
        await app.state.store.close()


def problem(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def create_app(store: Any = None, sink: Any = None, authenticator: Any = None,
               masking_key: bytes | None = None) -> FastAPI:
    app = FastAPI(title="VolumeOS Census Sync", version="0.1.0", lifespan=lifespan)
    app.state.configure = store is None
    app.state.store = store if store is not None else InMemoryCensusStore()
    app.state.sink = sink
    app.state.authenticator = authenticator
    app.state.sync = CensusSync(app.state.store, sink, masking_key or b"t" * 32) if sink is not None else None

    @app.middleware("http")
    async def private_response(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    async def authorize(request: Request, tenant_id: str) -> Claims | JSONResponse:
        try:
            claims = await app.state.authenticator.authenticate(request.headers.get("Authorization"))
        except AuthenticationError:
            return problem(401, "unauthorized", "valid OIDC credentials are required")
        device_id = request.headers.get("X-VolumeOS-Device", "")
        if (claims.tenant_id != tenant_id or claims.device_id != device_id or not claims.can_sync()):
            return problem(403, "scope_denied", "tenant, device, or field-agent scope is denied")
        return claims

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready():
        try:
            await app.state.store.ready()
        except Exception:
            return problem(503, "census_unavailable", "census store is not ready")
        return {"status": "ready"}

    @app.post("/v1/tenants/{tenant_id}/census/uploads", status_code=201)
    async def start(request: Request, tenant_id: str, body: StartUpload):
        claims = await authorize(request, tenant_id)
        if isinstance(claims, JSONResponse):
            return claims
        try:
            upload = Upload(tenant_id=tenant_id, device_id=claims.device_id, actor=claims.actor, **body.model_dump())
            return await app.state.sync.start(upload)
        except CensusError as exc:
            return problem(400, "invalid_upload", str(exc))

    @app.put("/v1/tenants/{tenant_id}/census/uploads/{upload_id}/chunks/{index}")
    async def put_chunk(request: Request, tenant_id: str, upload_id: str, index: int, body: PutChunk):
        claims = await authorize(request, tenant_id)
        if isinstance(claims, JSONResponse):
            return claims
        if index != body.index:
            return problem(400, "chunk_scope", "path and body chunk indexes differ")
        try:
            return await app.state.sync.put_chunk(
                tenant_id, claims.device_id, upload_id, index, body.encoded, body.expected_sha256,
            )
        except CensusError as exc:
            return problem(400, "invalid_chunk", str(exc))

    @app.get("/v1/tenants/{tenant_id}/census/uploads/{upload_id}")
    async def resume(request: Request, tenant_id: str, upload_id: str):
        claims = await authorize(request, tenant_id)
        if isinstance(claims, JSONResponse):
            return claims
        try:
            return await app.state.sync.resume(tenant_id, claims.device_id, upload_id)
        except CensusError as exc:
            return problem(404, "upload_unavailable", str(exc))

    @app.post("/v1/tenants/{tenant_id}/census/uploads/{upload_id}/complete")
    async def complete(request: Request, tenant_id: str, upload_id: str):
        claims = await authorize(request, tenant_id)
        if isinstance(claims, JSONResponse):
            return claims
        try:
            return await app.state.sync.complete(tenant_id, claims.device_id, upload_id)
        except CensusError as exc:
            return problem(400, "completion_denied", str(exc))

    return app


app = create_app()
