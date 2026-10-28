"""OIDC-authorized tenant report/log search API."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from auth import AuthenticationError, OIDCAuthenticator
from search import ReportSearch, SearchError, SearchRequest
from transport import OpenSearchTransport


@asynccontextmanager
async def lifespan(app: FastAPI):
    if app.state.configure:
        app.state.transport = OpenSearchTransport.from_env()
        app.state.authenticator = OIDCAuthenticator()
        app.state.search = ReportSearch(app.state.transport)
    yield
    if app.state.configure:
        await app.state.transport.close()


def create_app(transport: Any = None, authenticator: Any = None) -> FastAPI:
    app = FastAPI(title="VolumeOS Report Search", version="0.1.0", lifespan=lifespan)
    app.state.configure = transport is None
    app.state.transport = transport
    app.state.authenticator = authenticator
    app.state.search = ReportSearch(transport) if transport is not None else None

    @app.middleware("http")
    async def private_response(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready():
        try:
            await app.state.transport.ready()
        except Exception:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return {"status": "ready"}

    @app.get("/v1/tenants/{tenant_id}/search/{kind}")
    async def search(
        request: Request, tenant_id: str, kind: Literal["reports", "logs"], q: str,
        start_date: str, end_date: str, size: int = Query(default=50, ge=1, le=100),
    ):
        try:
            claims = await app.state.authenticator.authenticate(request.headers.get("Authorization"))
        except AuthenticationError:
            return JSONResponse(status_code=401, content={"error": {"code": "unauthorized"}})
        if claims.tenant_id != tenant_id or not claims.can("reader"):
            return JSONResponse(status_code=403, content={"error": {"code": "scope_denied"}})
        try:
            query = SearchRequest(kind, q, start_date, end_date, size)
            return await app.state.search.search(query, claims.tenant_id)
        except SearchError as exc:
            return JSONResponse(status_code=400, content={"error": {"code": "invalid_search", "message": str(exc)}})

    return app


app = create_app()
