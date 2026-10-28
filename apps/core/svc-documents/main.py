"""Internal tenant-scoped billing document and delivery API."""
from __future__ import annotations

import asyncio
import hmac
import os
from datetime import date, datetime, timezone
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from models import validate_recipient, validate_scope
from reports import SAFE_ID, BillingReports, FinancialExports, ReportError, validate_export_scope
from store import PostgresDocumentStore
from whatsapp import WhatsAppDocuments


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeliveryStart(InputModel):
    channel: Literal["whatsapp"]
    recipient: str


class PartnerContractStart(InputModel):
    contract_id: str = Field(pattern=SAFE_ID.pattern, max_length=128)
    partner_id: str = Field(pattern=SAFE_ID.pattern, max_length=128)
    offer: str = Field(pattern=r"^0[1-8]$")
    basis_points: int = Field(ge=0, le=10_000)
    effective_from: datetime
    effective_to: datetime | None = None

    @model_validator(mode="after")
    def valid_period(self):
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        return self


class ArExport(InputModel):
    as_of: date


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
    if app.state.configure:
        app.state.store = await PostgresDocumentStore.connect(os.environ.get("DATABASE_URL", ""))
        app.state.whatsapp = WhatsAppDocuments.from_env()
        app.state.service_token = os.environ.get("VOLUMEOS_SERVICE_TOKEN", "")
        app.state.billing_reports = BillingReports.from_env(app.state.service_token)
        app.state.financial_exports = FinancialExports.from_env()
    if len(app.state.service_token) < 32:
        raise RuntimeError("VOLUMEOS_SERVICE_TOKEN must contain at least 32 bytes")
    yield
    if app.state.configure:
        await app.state.store.close()


def problem(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def create_app(
    store: Any = None,
    whatsapp: Any = None,
    service_token: str | None = None,
    billing_reports: Any = None,
    financial_exports: Any = None,
) -> FastAPI:
    app = FastAPI(title="VolumeOS Billing Documents", version="0.1.0", lifespan=lifespan)
    app.state.configure = store is None
    app.state.store = store
    app.state.whatsapp = whatsapp
    app.state.service_token = service_token or ""
    app.state.billing_reports = billing_reports
    app.state.financial_exports = financial_exports

    @app.middleware("http")
    async def private_response(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def authorize(request: Request, tenant_id: str) -> JSONResponse | None:
        expected = app.state.service_token
        actual = request.headers.get("Authorization", "")
        if not expected or not hmac.compare_digest(actual, f"Bearer {expected}"):
            return problem(401, "unauthorized", "valid service authentication is required")
        if not hmac.compare_digest(request.headers.get("X-VolumeOS-Tenant", ""), tenant_id):
            return problem(403, "tenant_scope", "service tenant scope does not match the path")
        return None

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready():
        try:
            await app.state.store.ready()
        except Exception:
            return problem(503, "documents_unavailable", "document store is not ready")
        return {"status": "ready"}

    @app.get("/v1/tenants/{tenant_id}/documents")
    async def documents(request: Request, tenant_id: str, limit: int = Query(default=50, ge=1, le=100)):
        if denied := authorize(request, tenant_id):
            return denied
        try:
            validate_scope(tenant_id, "document", "invoice")
            return {"documents": await app.state.store.list_documents(tenant_id, limit)}
        except ValueError as exc:
            return problem(400, "invalid_request", str(exc))

    async def export_report(tenant_id: str, kind: str, report: dict[str, Any]):
        if app.state.financial_exports is None:
            return problem(503, "exports_unavailable", "retention-locked financial exports are not configured")
        try:
            metadata = await asyncio.to_thread(app.state.financial_exports.put, tenant_id, kind, report)
        except ReportError as exc:
            return problem(503, "export_unavailable", str(exc))
        return {"report": report, "export": metadata}

    @app.get("/v1/tenants/{tenant_id}/reports/ar-aging")
    async def ar_aging(request: Request, tenant_id: str, as_of: date | None = None):
        if denied := authorize(request, tenant_id):
            return denied
        try:
            validate_scope(tenant_id, "report", "invoice")
            report = await app.state.store.ar_aging(tenant_id, as_of or datetime.now(timezone.utc).date())
        except ValueError as exc:
            return problem(400, "invalid_report", str(exc))
        return report

    @app.post("/v1/tenants/{tenant_id}/reports/ar-aging/exports")
    async def export_ar_aging(request: Request, tenant_id: str, body: ArExport):
        if denied := authorize(request, tenant_id):
            return denied
        try:
            validate_scope(tenant_id, "report", "invoice")
            report = await app.state.store.ar_aging(tenant_id, body.as_of)
        except ValueError as exc:
            return problem(400, "invalid_report", str(exc))
        return await export_report(tenant_id, "ar-aging", report)

    @app.post("/v1/tenants/{tenant_id}/partner-contracts")
    async def create_partner_contract(request: Request, tenant_id: str, body: PartnerContractStart):
        if denied := authorize(request, tenant_id):
            return denied
        try:
            validate_scope(tenant_id, body.contract_id, "invoice")
            value = body.model_dump()
            value["tenant_id"] = tenant_id
            contract = await app.state.store.create_partner_contract(value)
        except ValueError as exc:
            return problem(409, "contract_conflict", str(exc))
        return JSONResponse(status_code=201, content=contract)

    @app.get("/v1/tenants/{tenant_id}/partner-contracts")
    async def partner_contracts(
        request: Request, tenant_id: str, partner_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        if denied := authorize(request, tenant_id):
            return denied
        try:
            validate_scope(tenant_id, "contract", "invoice")
            if partner_id is not None and not SAFE_ID.fullmatch(partner_id):
                raise ValueError("invalid partner id")
            return {"contracts": await app.state.store.list_partner_contracts(tenant_id, partner_id, limit)}
        except ValueError as exc:
            return problem(400, "invalid_request", str(exc))

    async def partner_report(tenant_id: str, partner_id: str, start: date, end: date):
        if not SAFE_ID.fullmatch(partner_id) or end < start or (end - start).days > 3660:
            raise ValueError("valid partner and a statement period no longer than ten years are required")
        return await app.state.store.partner_share_statement(tenant_id, partner_id, start, end)

    @app.get("/v1/tenants/{tenant_id}/reports/partner-share/{partner_id}")
    async def partner_share(request: Request, tenant_id: str, partner_id: str, start: date, end: date):
        if denied := authorize(request, tenant_id):
            return denied
        try:
            validate_scope(tenant_id, "report", "invoice")
            return await partner_report(tenant_id, partner_id, start, end)
        except ValueError as exc:
            return problem(400, "invalid_report", str(exc))

    @app.post("/v1/tenants/{tenant_id}/reports/partner-share/{partner_id}/exports")
    async def export_partner_share(request: Request, tenant_id: str, partner_id: str, body: PartnerExport):
        if denied := authorize(request, tenant_id):
            return denied
        try:
            validate_scope(tenant_id, "report", "invoice")
            report = await partner_report(tenant_id, partner_id, body.start, body.end)
        except ValueError as exc:
            return problem(400, "invalid_report", str(exc))
        return await export_report(tenant_id, "partner-share", report)

    @app.get("/v1/tenants/{tenant_id}/reports/trial-balance/{currency}")
    async def trial_balance(request: Request, tenant_id: str, currency: str):
        if denied := authorize(request, tenant_id):
            return denied
        if tenant_id != "platform" or app.state.billing_reports is None:
            return problem(403 if tenant_id != "platform" else 503, "report_unavailable", "platform TigerBeetle reporting is required")
        try:
            return await app.state.billing_reports.trial_balance(currency)
        except ValueError as exc:
            return problem(400, "invalid_request", str(exc))
        except ReportError as exc:
            return problem(503, "report_unavailable", str(exc))

    @app.post("/v1/tenants/{tenant_id}/reports/trial-balance/{currency}/exports")
    async def export_trial_balance(request: Request, tenant_id: str, currency: str):
        if denied := authorize(request, tenant_id):
            return denied
        if tenant_id != "platform" or app.state.billing_reports is None:
            return problem(403 if tenant_id != "platform" else 503, "report_unavailable", "platform TigerBeetle reporting is required")
        try:
            report = await app.state.billing_reports.trial_balance(currency)
        except ValueError as exc:
            return problem(400, "invalid_request", str(exc))
        except ReportError as exc:
            return problem(503, "report_unavailable", str(exc))
        return await export_report(tenant_id, "trial-balance", report)

    @app.get("/v1/tenants/{tenant_id}/financial-exports/{kind}/{digest}")
    async def financial_export(request: Request, tenant_id: str, kind: str, digest: str):
        if denied := authorize(request, tenant_id):
            return denied
        if app.state.financial_exports is None:
            return problem(503, "exports_unavailable", "retention-locked financial exports are not configured")
        try:
            validate_export_scope(tenant_id, kind, digest)
            content, metadata = await asyncio.to_thread(app.state.financial_exports.get, tenant_id, kind, digest)
        except ValueError as exc:
            return problem(400, "invalid_request", str(exc))
        except ReportError as exc:
            return problem(404, "export_unavailable", str(exc))
        return Response(
            content=content, media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{kind}-{digest[:12]}.json"',
                "X-Content-SHA256": metadata["sha256"],
                "X-Object-Lock-Mode": metadata["object_lock"],
                "X-Object-Lock-Retain-Until": metadata["retained_until"],
            },
        )

    @app.get("/v1/tenants/{tenant_id}/documents/{kind}/{document_id}")
    async def document_metadata(request: Request, tenant_id: str, kind: str, document_id: str):
        if denied := authorize(request, tenant_id):
            return denied
        try:
            document = await app.state.store.get_document(tenant_id, kind, document_id)
        except ValueError as exc:
            return problem(400, "invalid_request", str(exc))
        if document is None:
            return problem(404, "not_found", "document was not found in this tenant")
        return document.metadata()

    @app.get("/v1/tenants/{tenant_id}/documents/{kind}/{document_id}/pdf")
    async def document_pdf(request: Request, tenant_id: str, kind: str, document_id: str):
        if denied := authorize(request, tenant_id):
            return denied
        try:
            document = await app.state.store.get_document(tenant_id, kind, document_id)
        except ValueError as exc:
            return problem(400, "invalid_request", str(exc))
        if document is None:
            return problem(404, "not_found", "document was not found in this tenant")
        return Response(
            content=document.pdf, media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{document.document_number}.pdf"'},
        )

    @app.post("/v1/tenants/{tenant_id}/documents/{kind}/{document_id}/deliveries")
    async def deliver(request: Request, tenant_id: str, kind: str, document_id: str, body: DeliveryStart):
        if denied := authorize(request, tenant_id):
            return denied
        try:
            validate_scope(tenant_id, document_id, kind)
            validate_recipient(body.recipient)
        except ValueError as exc:
            return problem(400, "invalid_request", str(exc))
        document = await app.state.store.get_document(tenant_id, kind, document_id)
        if document is None:
            return problem(404, "not_found", "document was not found in this tenant")
        existing = await app.state.store.get_delivery(tenant_id, kind, document_id, body.recipient)
        if existing is not None:
            return JSONResponse(status_code=200, content=existing.json())
        try:
            provider_message_id = await app.state.whatsapp.deliver(document, body.recipient)
            delivery = await app.state.store.record_delivery(
                tenant_id, kind, document_id, body.recipient, provider_message_id,
            )
        except Exception:
            return problem(503, "delivery_unavailable", "WhatsApp did not confirm provider acceptance")
        return JSONResponse(status_code=202, content=delivery.json())

    return app


app = create_app()
