from fastapi.testclient import TestClient

from auth import AuthenticationError, Claims
from main import create_app


class Authenticator:
    async def authenticate(self, authorization):
        if authorization != "Bearer valid":
            raise AuthenticationError("denied")
        return Claims("tenant-a", "analyst-1", frozenset({"analyst"}))


class Transport:
    async def search(self, index, body):
        assert body["query"]["bool"]["filter"][0] == {"term": {"tenant_id.keyword": "tenant-a"}}
        return {"hits": {"hits": [{"_source": {"tenant_id": "tenant-a", "report_id": "r1", "title": "Revenue"}}]}}


def client():
    return TestClient(create_app(Transport(), Authenticator()))


def test_search_api_requires_tenant_reader_scope_and_returns_non_authoritative_rows():
    api = client()
    path = "/v1/tenants/tenant-a/search/reports"
    params = {"q": "revenue", "start_date": "2026-10-01", "end_date": "2026-10-28"}
    assert api.get(path, params=params).status_code == 401
    denied = api.get("/v1/tenants/tenant-b/search/reports", params=params, headers={"Authorization": "Bearer valid"})
    assert denied.status_code == 403
    response = api.get(path, params=params, headers={"Authorization": "Bearer valid"})
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [{"tenant_id": "tenant-a", "report_id": "r1", "title": "Revenue"}]
    assert response.json()["authoritative"] is False
    assert response.headers["cache-control"] == "no-store"


def test_search_api_rejects_unbounded_dates_and_queries():
    api = client()
    response = api.get(
        "/v1/tenants/tenant-a/search/logs",
        params={"q": "error", "start_date": "2020-01-01", "end_date": "2026-10-28"},
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 400


class PlatformAuthenticator:
    async def authenticate(self, authorization):
        return Claims("platform", "op", frozenset({"super-operator"}))


class InsightsTransport:
    def __init__(self, missing=False):
        self.missing, self.bodies = missing, []

    async def search(self, index, body):
        self.bodies.append((index, body))
        if self.missing:
            raise RuntimeError("NotFoundError(404, 'index_not_found_exception')")
        tenant = body["query"]["bool"]["filter"][0]["term"]["tenant_id.keyword"]
        return {"hits": {"hits": [
            {"_source": {"tenant_id": tenant, "month": "2026-11", "title": "Nov", "summary": "s", "metrics": {"paid_in_minor": 1}}},
            {"_source": {"tenant_id": "someone-else", "month": "2026-11", "metrics": {"paid_in_minor": 999}}}]}}


def test_insights_are_scoped_to_the_business_and_platform_operators_act_by_header():
    transport = InsightsTransport()
    api = TestClient(create_app(transport, Authenticator()))
    ok = api.get("/v1/tenants/tenant-a/insights", headers={"Authorization": "Bearer valid"})
    assert ok.status_code == 200 and ok.json()["months"] == [{"month": "2026-11", "title": "Nov", "summary": "s", "metrics": {"paid_in_minor": 1}, "updated_at": None}]
    assert api.get("/v1/tenants/tenant-b/insights", headers={"Authorization": "Bearer valid"}).status_code == 403
    assert transport.bodies[0][0] == "volumeos-reports-v1"
    platform = TestClient(create_app(InsightsTransport(), PlatformAuthenticator()))
    assert platform.get("/v1/tenants/tenant-a/insights", headers={"Authorization": "Bearer x", "X-VolumeOS-Tenant": "tenant-a"}).status_code == 200
    assert platform.get("/v1/tenants/tenant-a/insights", headers={"Authorization": "Bearer x", "X-VolumeOS-Tenant": "tenant-b"}).status_code == 403
    assert platform.get("/v1/tenants/platform/insights", headers={"Authorization": "Bearer x"}).status_code == 403


def test_insights_before_the_first_indexing_run_are_empty_not_an_error():
    api = TestClient(create_app(InsightsTransport(missing=True), Authenticator()))
    response = api.get("/v1/tenants/tenant-a/insights", headers={"Authorization": "Bearer valid"})
    assert response.status_code == 200 and response.json() == {"months": []}
