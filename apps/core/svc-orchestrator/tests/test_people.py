"""Platform operators (act-as) and people management: who may see and change which business's sign-ins."""
from typing import Any

from fastapi.testclient import TestClient

from auth import Claims
from main import create_app
from tests.test_api import FakeAuthenticator, FakeQAAssets, FakeWorkflows, auth

PERSON = "0b9a3c1e-1111-4222-8333-444455556666"


class FakePeople:
    def __init__(self):
        self.calls: list[tuple[Any, ...]] = []

    async def list(self, tenant_id):
        self.calls.append(("list", tenant_id))
        return {"people": []}

    async def create(self, tenant_id, payload):
        self.calls.append(("create", tenant_id, payload))
        return {"person": {"id": PERSON}, "sign_in_email": "sent"}

    async def update(self, tenant_id, person_id, payload):
        self.calls.append(("update", tenant_id, person_id, payload))
        return {"person": {"id": person_id}}


def client_for(claims: Claims):
    people = FakePeople()
    workflows = FakeWorkflows()
    app = create_app(workflows, FakeAuthenticator(claims), FakeQAAssets(), people=people)
    return TestClient(app), people, workflows


PLATFORM = Claims("platform", "platform-op", frozenset({"super-operator"}))
TENANT_OPERATOR = Claims("demo-tenant", "demo-op", frozenset({"super-operator"}))
NEW = {"email": "ada@acme.test", "first_name": "Ada", "last_name": "Obi", "role": "client-owner"}


def test_platform_operator_acts_on_the_business_in_the_header():
    client, people, workflows = client_for(PLATFORM)
    with client:
        response = client.get("/v1/people", headers={**auth(), "X-VolumeOS-Tenant": "acme-ltd"})
        assert response.status_code == 200
        client.get("/v1/engagements", headers={**auth(), "X-VolumeOS-Tenant": "acme-ltd"})
        bad = client.get("/v1/people", headers={**auth(), "X-VolumeOS-Tenant": "../x"})
    assert people.calls == [("list", "acme-ltd")]
    assert workflows.prefixes and all(prefix.startswith("engagement:acme-ltd:") or "acme-ltd" in prefix for prefix in workflows.prefixes)
    assert bad.status_code == 401


def test_a_business_cannot_reach_another_with_the_header():
    client, people, workflows = client_for(TENANT_OPERATOR)
    with client:
        client.get("/v1/people", headers={**auth(), "X-VolumeOS-Tenant": "acme-ltd"})
        client.get("/v1/engagements", headers={**auth(), "X-VolumeOS-Tenant": "acme-ltd"})
    assert people.calls == [("list", "demo-tenant")]
    assert all("acme-ltd" not in prefix for prefix in workflows.prefixes)


def test_only_super_operators_manage_people_and_only_platform_grants_super_operator():
    delivery = Claims("demo-tenant", "d1", frozenset({"delivery"}))
    client, people, _ = client_for(delivery)
    with client:
        assert client.get("/v1/people", headers=auth()).status_code == 403
        assert client.post("/v1/people", headers=auth(), json=NEW).status_code == 403

    client, people, _ = client_for(TENANT_OPERATOR)
    with client:
        assert client.post("/v1/people", headers=auth(), json=NEW).status_code == 201
        assert client.post("/v1/people", headers=auth(), json={**NEW, "role": "super-operator"}).status_code == 403
        assert client.patch(f"/v1/people/{PERSON}", headers=auth(), json={"role": "super-operator"}).status_code == 403
        assert client.patch(f"/v1/people/{PERSON}", headers=auth(), json={"enabled": False}).status_code == 200
        assert client.post("/v1/people", headers=auth(), json={**NEW, "role": "owner"}).status_code == 400
        assert client.patch(f"/v1/people/{PERSON}", headers=auth(), json={}).status_code == 400
    assert people.calls[0] == ("create", "demo-tenant", NEW)
    assert people.calls[-1] == ("update", "demo-tenant", PERSON, {"enabled": False})

    client, people, _ = client_for(PLATFORM)
    with client:
        response = client.post("/v1/people", headers={**auth(), "X-VolumeOS-Tenant": "acme-ltd"}, json={**NEW, "role": "super-operator"})
    assert response.status_code == 201 and people.calls[0][1] == "acme-ltd"


def test_nobody_changes_their_own_access():
    me = Claims("demo-tenant", PERSON, frozenset({"super-operator"}))
    client, people, _ = client_for(me)
    with client:
        assert client.patch(f"/v1/people/{PERSON}", headers=auth(), json={"enabled": False}).status_code == 409
    assert people.calls == []


def test_the_business_name_goes_to_the_sign_in_email_and_resending_is_its_own_request():
    client, people, _ = client_for(TENANT_OPERATOR)
    other = "1b9a3c1e-1111-4222-8333-444455556666"
    with client:
        assert client.post("/v1/people", headers=auth(), json={**NEW, "role": "sales", "business_name": "Demo Tenant Ltd"}).status_code == 201
        assert client.patch(f"/v1/people/{other}", headers=auth(), json={"send_sign_in": True, "business_name": "Demo Tenant Ltd"}).status_code == 200
        assert client.patch(f"/v1/people/{other}", headers=auth(), json={"send_sign_in": True, "enabled": False}).status_code == 400
        assert client.patch(f"/v1/people/{other}", headers=auth(), json={"send_sign_in": False}).status_code == 400
    assert people.calls[0] == ("create", "demo-tenant", {**NEW, "role": "sales", "business_name": "Demo Tenant Ltd"})
    assert people.calls[1] == ("update", "demo-tenant", other, {"send_sign_in": True, "business_name": "Demo Tenant Ltd"})
    assert len(people.calls) == 2
