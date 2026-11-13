"""Hardening from the 2026-11-09 black-box pass (BB-02, BB-07, BB-10, BB-11, BB-14, BB-16)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

import workflows as workflows_module
from auth import Claims
from main import create_app
from workflows import TemporalWorkflows
from tests.test_api import FakeAuthenticator, FakeQAAssets, FakeWorkflows, auth

ENGAGEMENT = {"engagement_id": "eng-1", "offer": "27", "title": "Website plan", "scope": "Plan a website", "requested_outcome": ""}


def client_for(roles: set[str], money=None):
    app = create_app(FakeWorkflows(), FakeAuthenticator(Claims("tenant-a", "user-1", frozenset(roles))), FakeQAAssets(), money)
    return TestClient(app)


# BB-10: the role is checked before the body, so a caller without the role learns nothing about the request format.
@pytest.mark.parametrize("method,path", [
    ("post", "/v1/campaigns"), ("post", "/v1/studio/packs"), ("post", "/v1/offers/jobs"), ("post", "/v1/engagements"),
    ("post", "/v1/billing/cycles"), ("post", "/v1/billing/recovery"), ("post", "/v1/billing/partner-contracts"),
    ("post", "/v1/engagements/eng-1/review"), ("post", "/v1/offers/jobs/job-1/review"),
])
def test_a_reader_is_refused_before_the_body_is_validated(method, path):
    with client_for({"reader"}) as client:
        response = getattr(client, method)(path, json={}, headers=auth())
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_no_token_is_refused_before_the_body_is_validated():
    with client_for({"delivery"}) as client:
        response = client.post("/v1/engagements", json={})
    assert response.status_code == 401


def test_either_role_routes_accept_either():
    with client_for({"analyst"}) as client:
        response = client.post("/v1/engagements/eng-1/review", json={}, headers=auth())
    assert response.status_code == 400  # allowed in, then the empty body is refused


# BB-16: a refusal names the fields to fix (never their values).
def test_validation_errors_name_the_fields():
    with client_for({"delivery"}) as client:
        response = client.post("/v1/engagements", json={**ENGAGEMENT, "title": "", "offer": "12"}, headers=auth())
    body = response.json()["error"]
    assert response.status_code == 400
    assert set(body["fields"]) == {"title", "offer"}
    assert "title" in body["message"] and "offer" in body["message"]


# BB-14: invisible characters are refused in every text field, including nested payloads; normal text is not.
@pytest.mark.parametrize("bad", ["\x00", "a\x00b", "evil‮txt", "x\x1by", "⁦hidden"])
def test_hidden_characters_are_refused(bad):
    with client_for({"delivery"}) as client:
        response = client.post("/v1/engagements", json={**ENGAGEMENT, "title": bad}, headers=auth())
    assert response.status_code == 400


def test_line_breaks_and_unicode_text_are_fine():
    with client_for({"delivery"}) as client:
        response = client.post("/v1/engagements", json={**ENGAGEMENT, "scope": "Line one\nLine two\tand ₦ 😀 Ìbàdàn"}, headers=auth())
    assert response.status_code == 202


# BB-11: an offer job whose AI ceiling is below its estimate is refused up front, not accepted and then failed.
def offer_job(**payload):
    return {"job_id": "job-1", "offer": "08", "amount_minor": 10000, "currency": "NGN",
            "payload": {"cost_estimate_minor": 9000, "job_ceiling_minor": 1000, **payload}}


def test_offer_job_ceiling_below_estimate_is_refused():
    with client_for({"delivery"}) as client:
        assert client.post("/v1/offers/jobs", json=offer_job(), headers=auth()).status_code == 400
        assert client.post("/v1/offers/jobs", json=offer_job(job_ceiling_minor=9000), headers=auth()).status_code == 202


# BB-07: amounts are real integers (no "100" strings or booleans) and the return address must be this site.
def money_stub():
    money = SimpleNamespace(topup=AsyncMock(return_value={"status": "pending"}))
    return money


TOPUP = {"amount_minor": 10000, "currency": "NGN", "customer_email": "a@example.com", "redirect_url": "https://testserver/billing/callback"}


@pytest.mark.parametrize("amount", ["100", True, 10.5])
def test_amounts_must_be_whole_numbers(amount):
    with client_for({"client-owner"}, money_stub()) as client:
        response = client.post("/v1/billing/topups/paystack/ref-1", json={**TOPUP, "amount_minor": amount}, headers={**auth(), "Host": "testserver"})
    assert response.status_code == 400


def test_topup_return_address_must_be_this_site():
    money = money_stub()
    with client_for({"client-owner"}, money) as client:
        evil = client.post("/v1/billing/topups/paystack/ref-1", json={**TOPUP, "redirect_url": "https://evil.example.com/x"}, headers=auth())
        good = client.post("/v1/billing/topups/paystack/ref-1", json=TOPUP, headers=auth())
    assert evil.status_code == 400 and "this site" in evil.json()["error"]["message"]
    assert good.status_code == 201
    assert money.topup.await_count == 1


def test_topup_return_address_can_be_configured(monkeypatch):
    monkeypatch.setenv("TOPUP_REDIRECT_ORIGINS", "https://console.example.com, https://other.example.com/")
    with client_for({"client-owner"}, money_stub()) as client:
        ok = client.post("/v1/billing/topups/paystack/ref-1", json={**TOPUP, "redirect_url": "https://other.example.com/back"}, headers=auth())
        host = client.post("/v1/billing/topups/paystack/ref-1", json=TOPUP, headers=auth())
    assert ok.status_code == 201
    assert host.status_code == 400  # with a list configured, only the list counts


# BB-02: one workflow whose worker can't answer no longer fails or stalls the whole list.
class Execution:
    def __init__(self, workflow_id):
        self.id = workflow_id
        self.run_id = "run"
        self.status = WorkflowExecutionStatus.RUNNING
        self.start_time = None


class Handle:
    def __init__(self, behaviour):
        self.behaviour = behaviour

    async def describe(self):
        return SimpleNamespace(status=WorkflowExecutionStatus.RUNNING)

    async def query(self, _name):
        if self.behaviour == "hang":
            await asyncio.sleep(60)
        if self.behaviour == "error":
            raise RPCError("deadline", RPCStatusCode.DEADLINE_EXCEEDED, b"")
        if self.behaviour == "gone":
            raise RPCError("gone", RPCStatusCode.NOT_FOUND, b"")
        return {"tenant_id": "tenant-a", "campaign_id": self.behaviour, "status": "drafting"}


class Client:
    def __init__(self, ids):
        self.ids = ids

    def list_workflows(self, _query):
        async def generator():
            for workflow_id in self.ids:
                yield Execution(workflow_id)
        return generator()

    def get_workflow_handle(self, workflow_id, run_id=None):
        return Handle(workflow_id.rsplit(":", 1)[1])


def test_a_workflow_that_cant_answer_is_listed_as_unavailable(monkeypatch):
    monkeypatch.setattr(workflows_module, "QUERY_SECONDS", 0.2)
    ids = ["campaign:tenant-a:ok", "campaign:tenant-a:hang", "campaign:tenant-a:error", "campaign:tenant-a:gone"]
    result = asyncio.run(TemporalWorkflows(Client(ids)).list("campaign:tenant-a:"))
    by_id = {item["campaign_id"]: item for item in result}
    assert set(by_id) == {"ok", "hang", "error"}  # the vanished one is skipped
    assert by_id["ok"]["status"] == "drafting"
    for stuck in ("hang", "error"):
        assert by_id[stuck]["status"] == "unavailable" and by_id[stuck]["tenant_id"] == "tenant-a" and by_id[stuck]["error"]


def test_queries_run_side_by_side(monkeypatch):
    monkeypatch.setattr(workflows_module, "QUERY_SECONDS", 0.5)
    ids = [f"campaign:tenant-a:hang{i}" for i in range(10)]
    client = Client(ids)
    client.get_workflow_handle = lambda workflow_id, run_id=None: Handle("hang")
    started = asyncio.get_event_loop_policy().new_event_loop()
    try:
        begin = started.time()
        result = started.run_until_complete(TemporalWorkflows(client).list("campaign:tenant-a:"))
        elapsed = started.time() - begin
    finally:
        started.close()
    assert len(result) == 10 and elapsed < 2.0  # ten stuck workflows cost one time limit, not ten
