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
