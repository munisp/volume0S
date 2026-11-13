"""svc-campaigns: campaign drafts, contact lists and sending for VolumeOS (Offer 01 Come-Back Engine), plus the
billing worker's payment reminders.

Callers (all with the service token and X-VolumeOS-Tenant): campaign-worker (POST /v1/drafts, POST /v1/dispatches),
billing-worker (POST /v1/dunning-nudges) and svc-orchestrator for the console (contacts, draft preview, results).
One public route: GET/POST /v1/unsubscribe?t=<signed token> from the link in every campaign email.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import os
import re
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from datetime import datetime, timezone

from channels import (ChannelError, EmailMessage, Postiz, ResendEmail, Services, WhatsAppTemplate, is_test_address,
                      is_test_phone, read_unsubscribe_token, seal, unseal, unsubscribe_token)
from drafting import DraftingError, OllamaDrafter, personalise
from store import PostgresCampaignStore

SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE = re.compile(r"^\+[1-9]\d{7,14}$")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://volumeos.newfire.app").rstrip("/")
POSTIZ_PUBLIC_URL = os.environ.get("POSTIZ_PUBLIC_URL", "").rstrip("/")  # where people open Postiz to connect accounts


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftStart(InputModel):
    campaign_id: str = Field(pattern=SAFE_ID.pattern)
    brief: str = Field(min_length=1, max_length=10_000)


class DispatchStart(InputModel):
    campaign_id: str = Field(pattern=SAFE_ID.pattern)
    cycle_id: str = Field(pattern=SAFE_ID.pattern)
    draft_id: str = Field(pattern=SAFE_ID.pattern)
    approval_id: str = Field(min_length=1, max_length=128)
    amount_minor: int = Field(default=0, ge=0, le=1_000_000_000_000)  # the campaign's charge, for reporting
    currency: Literal["NGN", "USD"] = "NGN"


class Contact(InputModel):
    first_name: str = Field(default="", max_length=80)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=20)
    email_consent: bool = False
    whatsapp_consent: bool = False

    @field_validator("first_name")
    @classmethod
    def plain_name(cls, value: str) -> str:
        value = value.strip()
        if any(not ch.isprintable() for ch in value) or "{{" in value or "http" in value.lower():
            raise ValueError("first names must be plain text")
        return value

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip().lower()
        if not EMAIL.fullmatch(value):
            raise ValueError("not an email address")
        return value

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = re.sub(r"[\s()-]", "", value)
        if value.startswith("0") and len(value) == 11:  # Nigerian local format 0803... → +234803...
            value = "+234" + value[1:]
        if not PHONE.fullmatch(value):
            raise ValueError("phone numbers need the country code, e.g. +2348031234567")
        return value


class ContactImport(InputModel):
    contacts: list[Contact] = Field(min_length=1, max_length=5000)


class SocialConnect(InputModel):
    api_key: str = Field(min_length=10, max_length=500, pattern=r"^[A-Za-z0-9._~+/=-]+$")


class DunningNudge(InputModel):
    channel: Literal["email", "sms", "whatsapp"]
    subscription_id: str = Field(pattern=SAFE_ID.pattern)
    cycle: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    attempt: int = Field(ge=1, le=10)
    amount_minor: str = Field(pattern=r"^\d{1,15}$")
    currency: Literal["NGN", "USD"]


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def create_app(store: Any = None, drafter: Any = None, email: Any = None, whatsapp: Any = None, services: Any = None,
               postiz: Any = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.store is None:
            app.state.store = await PostgresCampaignStore.connect(os.environ["DATABASE_URL"])
        if app.state.drafter is None:
            app.state.drafter = OllamaDrafter.from_env()
        if app.state.email is None:
            app.state.email = ResendEmail.from_env()
        if app.state.whatsapp is None:
            app.state.whatsapp = WhatsAppTemplate.from_env()
        if app.state.services is None:
            app.state.services = Services.from_env()
        if app.state.postiz is None:
            app.state.postiz = Postiz.from_env()
        yield
        if hasattr(app.state.store, "close"):
            await app.state.store.close()

    app = FastAPI(title="VolumeOS campaigns", lifespan=lifespan)
    app.state.store, app.state.drafter, app.state.email = store, drafter, email
    app.state.whatsapp, app.state.services, app.state.postiz = whatsapp, services, postiz
    token = os.environ.get("VOLUMEOS_SERVICE_TOKEN", "")

    def caller(request: Request) -> str | JSONResponse:
        """Service token + business header; returns the tenant id or the refusal."""
        provided = request.headers.get("authorization", "").removeprefix("Bearer ")
        if len(token) < 32 or not hmac.compare_digest(provided.encode(), token.encode()):
            return error(401, "unauthorized", "service token required")
        tenant = request.headers.get("x-volumeos-tenant", "")
        if not SAFE_ID.fullmatch(tenant) or tenant == "platform":
            return error(400, "invalid_tenant", "a business is required")
        return tenant

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready():
        await app.state.store.ready()
        return {"status": "ready", "drafting": app.state.drafter is not None, "email": app.state.email is not None,
                "whatsapp": app.state.whatsapp is not None, "social": app.state.postiz is not None}

    # Social accounts (Postiz) ---------------------------------------------------------------------------------
    async def social_accounts(tenant: str) -> tuple[str | None, list[dict]]:
        sealed = await app.state.store.social_key(tenant)
        if not sealed or app.state.postiz is None:
            return None, []
        key = unseal(sealed)
        return key, await app.state.postiz.integrations(key)

    @app.get("/v1/social")
    async def social(request: Request):
        tenant = caller(request)
        if isinstance(tenant, JSONResponse):
            return tenant
        if app.state.postiz is None:
            return {"available": False, "connected": False, "accounts": []}
        try:
            key, accounts = await social_accounts(tenant)
        except ChannelError as exc:
            return {"available": True, "connected": True, "accounts": [], "problem": str(exc), "postiz_url": POSTIZ_PUBLIC_URL or app.state.postiz.base}
        return {"available": True, "connected": key is not None, "accounts": accounts, "postiz_url": POSTIZ_PUBLIC_URL or app.state.postiz.base}

    @app.put("/v1/social")
    async def connect_social(body: SocialConnect, request: Request):
        tenant = caller(request)
        if isinstance(tenant, JSONResponse):
            return tenant
        if app.state.postiz is None:
            return error(503, "social_not_configured", "social posting isn't set up on this deployment")
        try:
            accounts = await app.state.postiz.integrations(body.api_key)  # refuse a key Postiz doesn't accept
        except ChannelError as exc:
            return error(400, "invalid_postiz_key", str(exc))
        await app.state.store.save_social_key(tenant, seal(body.api_key))
        return {"connected": True, "accounts": accounts}

    @app.delete("/v1/social")
    async def disconnect_social(request: Request):
        tenant = caller(request)
        if isinstance(tenant, JSONResponse):
            return tenant
        await app.state.store.delete_social_key(tenant)
        return {"connected": False}

    # Drafts ---------------------------------------------------------------------------------------------------
    @app.post("/v1/drafts")
    async def create_draft(body: DraftStart, request: Request):
        tenant = caller(request)
        if isinstance(tenant, JSONResponse):
            return tenant
        existing = await app.state.store.draft_for_campaign(tenant, body.campaign_id)
        if existing:  # the worker retries: one campaign, one draft
            return existing
        if app.state.drafter is None:
            return error(503, "drafting_not_configured", "campaign drafting isn't set up on this deployment")
        try:
            business = await app.state.services.business_name(tenant)
            content = await app.state.drafter.draft(business, body.brief)
        except DraftingError as exc:
            return error(503, "drafting_failed", str(exc))
        draft_id = "draft:" + hashlib.blake2s(f"{tenant}\x1f{body.campaign_id}".encode(), digest_size=12).hexdigest()
        return await app.state.store.save_draft(tenant, {**content, "draft_id": draft_id, "campaign_id": body.campaign_id,
                                                          "model": getattr(app.state.drafter, "model", "unknown")})

    @app.get("/v1/drafts/{draft_id}")
    async def get_draft(draft_id: str, request: Request):
        tenant = caller(request)
        if isinstance(tenant, JSONResponse):
            return tenant
        draft = await app.state.store.get_draft(tenant, draft_id)
        return draft or error(404, "not_found", "no such draft in this business")

    @app.get("/v1/campaigns/{campaign_id}")
    async def campaign_detail(campaign_id: str, request: Request):
        tenant = caller(request)
        if isinstance(tenant, JSONResponse):
            return tenant
        if not SAFE_ID.fullmatch(campaign_id):
            return error(400, "invalid_request", "invalid campaign id")
        return {"draft": await app.state.store.draft_for_campaign(tenant, campaign_id),
                "results": await app.state.store.dispatch_summary(tenant, campaign_id)}

    # Contacts -------------------------------------------------------------------------------------------------
    @app.get("/v1/contacts")
    async def contacts(request: Request, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
        tenant = caller(request)
        if isinstance(tenant, JSONResponse):
            return tenant
        return await app.state.store.list_contacts(tenant, limit, offset)

    @app.post("/v1/contacts/import")
    async def import_contacts(body: ContactImport, request: Request):
        tenant = caller(request)
        if isinstance(tenant, JSONResponse):
            return tenant
        rows = [c.model_dump() for c in body.contacts if c.email or c.phone]
        if not rows:
            return error(400, "invalid_request", "every contact needs an email or a phone number")
        return await app.state.store.import_contacts(tenant, rows)

    @app.delete("/v1/contacts/{contact_id}")
    async def delete_contact(contact_id: str, request: Request):
        tenant = caller(request)
        if isinstance(tenant, JSONResponse):
            return tenant
        if not re.fullmatch(r"[0-9a-f-]{36}", contact_id):
            return error(404, "not_found", "no such contact in this business")
        return {"deleted": True} if await app.state.store.delete_contact(tenant, contact_id) else error(404, "not_found", "no such contact in this business")

    # Sending --------------------------------------------------------------------------------------------------
    @app.post("/v1/dispatches")
    async def dispatch(body: DispatchStart, request: Request):
        tenant = caller(request)
        if isinstance(tenant, JSONResponse):
            return tenant
        store = app.state.store
        draft = await store.get_draft(tenant, body.draft_id)
        if not draft or draft["campaign_id"] != body.campaign_id:
            return error(404, "not_found", "the approved draft isn't in this business")
        dispatch_id = "dispatch:" + hashlib.blake2s(f"{tenant}\x1f{body.campaign_id}\x1f{body.cycle_id}".encode(), digest_size=12).hexdigest()
        finished = await store.begin_dispatch(tenant, body.model_dump(), dispatch_id)
        if finished:
            return finished
        audience = await store.audience(tenant)
        try:
            social_key, social_accounts_list = await social_accounts(tenant)
        except ChannelError:
            social_key, social_accounts_list = None, []
        live_accounts = [a for a in social_accounts_list if not a["disabled"]] if draft.get("social_text") else []
        if not audience and not live_accounts:
            return error(409, "no_audience", "this business has no contacts who agreed to hear from it and no connected social "
                         "accounts; import contacts or connect social accounts first")
        details = await app.state.services.business(tenant)
        business = str(details.get("name", ""))
        # Checked once up front so the reason is plain; svc-tenancy still decides every single send below.
        if details.get("state") != "active":
            return error(409, "business_not_active", f"{business or 'This business'} is {details.get('state', 'not active')}: "
                         "campaigns can only be sent once it's active. Activate it under Client, then start the campaign again.")
        if details.get("outbound_suspended"):
            return error(409, "outbound_suspended", "Outbound messages are paused for this business (kill switch); nothing was sent.")
        done = await store.sent_before(tenant, dispatch_id)
        counts: dict[str, dict[str, int]] = {}

        async def attempt(contact: dict[str, Any], channel: str, recipient: str, send) -> None:
            if done.get((contact["id"], channel)) in {"sent", "test_address", "blocked"}:
                status = done[(contact["id"], channel)]
            else:
                allowed, reason = await app.state.services.may_send(tenant, channel, recipient)
                if not allowed:
                    status, provider, detail = "blocked", "", reason
                else:
                    try:
                        status, provider, detail = await send()
                    except ChannelError as exc:
                        status, provider, detail = "failed", "", str(exc)
                await store.record_send(tenant, dispatch_id, contact["id"], channel, status, provider, detail)
            bucket = counts.setdefault(channel, {"success": 0, "failure": 0})
            # Blocked (suppressed / kill switch) is a correct outcome, not a delivery failure, but it isn't a send.
            if status in {"sent", "test_address"}:
                bucket["success"] += 1
            elif status == "failed":
                bucket["failure"] += 1

        for contact in audience:
            if contact["email"] and contact["email_consent"] and app.state.email is not None:
                async def send_email(c=contact):
                    if is_test_address(c["email"]):
                        return "test_address", "", "test address: not emailed"
                    message = EmailMessage(
                        to=c["email"], business=business or "VolumeOS",
                        subject=draft["subject"], body=personalise(draft["email_body"], c["first_name"]),
                        unsubscribe_url=f"{PUBLIC_URL}/v1/unsubscribe?t={unsubscribe_token(tenant, c['email'])}")
                    return "sent", await app.state.email.send(message), ""
                await attempt(contact, "email", contact["email"], send_email)
            if contact["phone"] and contact["whatsapp_consent"] and app.state.whatsapp is not None:
                async def send_whatsapp(c=contact):
                    if is_test_phone(c["phone"]):
                        return "test_address", "", "test number: not messaged"
                    return "sent", await app.state.whatsapp.send(c["phone"], personalise(draft["whatsapp_text"], c["first_name"])), ""
                await attempt(contact, "whatsapp", contact["phone"], send_whatsapp)

        if live_accounts and social_key:
            posted = await store.social_posted(tenant, dispatch_id)
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            for account in live_accounts:
                status = posted.get(account["id"])
                if status != "sent":
                    try:
                        provider = await app.state.postiz.post_now(social_key, account, draft["social_text"], now)
                        status, detail = "sent", ""
                    except ChannelError as exc:
                        status, provider, detail = "failed", "", str(exc)
                    await store.record_social_post(tenant, dispatch_id, account["id"], account["network"], status, provider, detail)
                bucket = counts.setdefault("social", {"success": 0, "failure": 0})
                bucket["success" if status == "sent" else "failure"] += 1

        channels = [{"channel": ch, **n} for ch, n in sorted(counts.items()) if n["success"] + n["failure"] > 0]
        if not channels:
            return error(409, "nothing_sent", "every contact was suppressed, or no channel is set up for them")
        result = {"dispatch_id": dispatch_id, "cycle_id": body.cycle_id, "channels": channels}
        await store.finish_dispatch(tenant, dispatch_id, result)
        return result

    @app.post("/v1/dunning-nudges")
    async def dunning_nudge(body: DunningNudge, request: Request):
        tenant = caller(request)
        if isinstance(tenant, JSONResponse):
            return tenant
        nudge_id = request.headers.get("idempotency-key") or f"{body.subscription_id}:{body.cycle}:{body.attempt}:{body.channel}"
        if await app.state.store.nudge_sent(tenant, nudge_id):
            return {"status": "already_sent"}
        if body.channel != "email" or app.state.email is None:
            return {"status": "skipped", "reason": f"{body.channel} reminders aren't set up"}
        owners = await app.state.services.billing_contacts(tenant)
        business = await app.state.services.business_name(tenant)
        amount = int(body.amount_minor) / 100
        symbol = "₦" if body.currency == "NGN" else "$"
        sent = 0
        for owner in owners:
            if is_test_address(owner["email"]):
                continue
            await app.state.email.send(EmailMessage(
                to=owner["email"], business="VolumeOS", subject=f"Top up to keep {business or 'your services'} running",
                body=(f"Hello {owner.get('first_name') or ''},\n\nYour {body.cycle} bill of {symbol}{amount:,.2f} is waiting for "
                      f"funds in your VolumeOS balance. Top up from Wallet & billing at {PUBLIC_URL} and the bill settles "
                      f"automatically. If it stays unpaid, services will be paused.\n\nReminder {body.attempt}."),
                unsubscribe_url=f"{PUBLIC_URL}/account"))
            sent += 1
        await app.state.store.record_nudge(tenant, nudge_id, body.channel, sent)
        return {"status": "sent", "recipients": sent}

    # Public unsubscribe -------------------------------------------------------------------------------------------
    # GET only asks: mail scanners (Safe Links and the like) open every link in an email, and must not unsubscribe
    # anyone. The button, and mail apps' one-click unsubscribe (RFC 8058, List-Unsubscribe-Post), POST it.
    async def unsubscribe(request: Request, t: str):
        found = read_unsubscribe_token(t)
        page = '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Unsubscribe</title>' \
               '<body style="font-family:system-ui,sans-serif;max-width:460px;margin:12vh auto;padding:0 16px;color:#1a1a1a">'
        if not found:
            return HTMLResponse(page + "<h1>Link not recognised</h1><p>This unsubscribe link isn't valid.</p>", status_code=400)
        if request.method == "GET":
            return HTMLResponse(page + "<h1>Unsubscribe?</h1><p>Stop getting these emails from this business.</p>"
                                f'<form method="post" action="?t={html.escape(t)}"><button type="submit" '
                                'style="font:inherit;padding:10px 18px;border-radius:8px;border:1px solid #1a1a1a;'
                                'background:#1a1a1a;color:#fff;cursor:pointer">Unsubscribe</button></form>')
        tenant, address = found
        try:
            await app.state.services.suppress(tenant, "email", address, "unsubscribe link")
        except Exception:
            return HTMLResponse(page + "<h1>Please try again</h1><p>We couldn't record that just now.</p>", status_code=503)
        return HTMLResponse(page + "<h1>You're unsubscribed</h1><p>You won't get these emails from this business again.</p>")

    app.add_api_route("/v1/unsubscribe", unsubscribe, methods=["GET", "POST"], response_class=HTMLResponse)
    return app


app = create_app()
