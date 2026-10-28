from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
import os
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from auth import AuthenticationError, Claims, OIDCAuthenticator, require_roles
from conversations import ConversationError, ConversationService
from providers import HTTPBookingAdapter, PrivacyAIClient, WhatsAppCloudSender
from store import PostgresConversationStore


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewInput(Input):
    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=1, max_length=1000)


class BookingInput(Input):
    slot_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]*$")


class SuppressionInput(Input):
    reason: str = Field(min_length=1, max_length=1000)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if app.state.configure:
        store = await PostgresConversationStore.connect(os.environ.get("DATABASE_URL", ""))
        app.state.store = store
        app.state.oidc = OIDCAuthenticator()
        app.state.http = __import__("httpx").AsyncClient(timeout=45)
        token = os.environ.get("VOLUMEOS_SERVICE_TOKEN", "")
        ai = PrivacyAIClient(app.state.http, os.environ.get("PRIVACY_SERVICE_URL", ""), token)
        sender = WhatsAppCloudSender(app.state.http, os.environ.get("WHATSAPP_API_URL", ""),
                                     os.environ.get("WHATSAPP_ACCESS_TOKEN", ""))
        booking = HTTPBookingAdapter(app.state.http, os.environ.get("BOOKING_SERVICE_URL", ""), token)
        app.state.service = ConversationService(store, ai, sender, booking,
                                                os.environ.get("SUBJECT_DIGEST_KEY", "").encode())
        app.state.app_secret = os.environ.get("WHATSAPP_APP_SECRET", "")
        app.state.model_version = os.environ.get("FRONT_DESK_MODEL_VERSION", "")
        if not app.state.app_secret or not app.state.model_version:
            raise RuntimeError("WHATSAPP_APP_SECRET and FRONT_DESK_MODEL_VERSION are required")
    yield
    if app.state.configure:
        await app.state.http.close()
        await app.state.store.close()


def create_app(store=None, service=None, authenticator=None, app_secret: str = "test-secret",
               model_version: str = "model-v1") -> FastAPI:
    app = FastAPI(title="VolumeOS AI Front Desk", version="0.1.0", lifespan=lifespan)
    app.state.configure = service is None
    app.state.store = store
    app.state.service = service
    app.state.oidc = authenticator
    app.state.app_secret = app_secret
    app.state.model_version = model_version

    @app.middleware("http")
    async def private_response(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(ConversationError)
    async def conversation_error(_request: Request, error: ConversationError):
        message = str(error)
        status = 401 if "signature" in message else (404 if "not found" in message else 409)
        return JSONResponse(status_code=status, content={"detail": message})

    async def principal(request: Request) -> Claims:
        try:
            return await request.app.state.oidc.authenticate(request.headers.get("Authorization"))
        except AuthenticationError as exc:
            raise HTTPException(401, "valid OIDC credentials are required") from exc

    def svc(request: Request) -> ConversationService:
        if request.app.state.service is None:
            raise HTTPException(503, "conversation service unavailable")
        return request.app.state.service

    @app.get("/healthz")
    async def health():
        return {"ok": True}

    @app.get("/readyz")
    async def ready():
        if app.state.store is None:
            raise HTTPException(503, "conversation store unavailable")
        await app.state.store.ready()
        return {"ok": True}

    @app.post("/v1/webhooks/whatsapp/{tenant_id}", status_code=202)
    async def whatsapp_webhook(tenant_id: str, request: Request):
        raw = await request.body()
        if len(raw) > 64 * 1024:
            raise HTTPException(413, "WhatsApp webhook exceeds its size bound")
        return await svc(request).ingest(
            tenant_id, raw, request.headers.get("X-Hub-Signature-256", ""),
            app.state.app_secret, app.state.model_version,
        )

    @app.get("/v1/conversations")
    async def conversation_queue(request: Request):
        actor = require_roles(await principal(request), "super-operator", "front-desk-agent", "front-desk-reviewer", "auditor")
        return {"conversations": await svc(request).queue(actor.tenant_id)}

    @app.get("/v1/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str, request: Request):
        actor = require_roles(await principal(request), "super-operator", "front-desk-agent", "front-desk-reviewer", "auditor")
        return asdict(await svc(request)._conversation(actor.tenant_id, conversation_id))

    @app.get("/v1/conversations/{conversation_id}/drafts/{draft_sha256}")
    async def get_draft(conversation_id: str, draft_sha256: str, request: Request):
        actor = require_roles(await principal(request), "super-operator", "front-desk-agent", "front-desk-reviewer", "auditor")
        return {**asdict(await svc(request)._draft(actor.tenant_id, conversation_id, draft_sha256)),
                "draft_sha256": draft_sha256}

    @app.post("/v1/conversations/{conversation_id}/drafts/{draft_sha256}/review")
    async def review(conversation_id: str, draft_sha256: str, body: ReviewInput, request: Request):
        actor = require_roles(await principal(request), "super-operator", "front-desk-reviewer")
        draft = await svc(request).review(actor.tenant_id, conversation_id, draft_sha256, actor.actor,
                                          body.decision, body.reason)
        return {**asdict(draft), "draft_sha256": draft.digest}

    @app.post("/v1/conversations/{conversation_id}/drafts/{draft_sha256}/send")
    async def send(conversation_id: str, draft_sha256: str, request: Request):
        actor = require_roles(await principal(request), "super-operator", "front-desk-agent")
        return await svc(request).send(actor.tenant_id, conversation_id, draft_sha256)

    @app.post("/v1/conversations/{conversation_id}/bookings", status_code=201)
    async def book(conversation_id: str, body: BookingInput, request: Request):
        actor = require_roles(await principal(request), "super-operator", "front-desk-agent")
        return await svc(request).book(actor.tenant_id, conversation_id, body.slot_id)

    @app.post("/v1/conversations/{conversation_id}/suppression")
    async def suppress(conversation_id: str, body: SuppressionInput, request: Request):
        actor = require_roles(await principal(request), "super-operator", "privacy-officer")
        await svc(request).suppress(actor.tenant_id, conversation_id, actor.actor, body.reason)
        return {"status": "suppressed"}

    return app


app = create_app()
