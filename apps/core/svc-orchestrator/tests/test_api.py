from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from temporalio.common import WorkflowIDReusePolicy

from auth import AuthenticationError, Claims
from main import create_app
from workflows import TemporalWorkflows


class FakeAuthenticator:
    def __init__(self, claims: Claims | None = None):
        self.claims = claims or Claims("tenant-a", "operator-1", frozenset({"delivery"}))

    async def authenticate(self, authorization: str | None) -> Claims:
        if authorization != "Bearer valid":
            raise AuthenticationError("invalid")
        return self.claims


class FakeWorkflows:
    def __init__(self):
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.started = []
        self.signals = []
        self.prefixes = []

    async def ready(self):
        return None

    async def list(self, prefix: str):
        self.prefixes.append(prefix)
        return [value for key, value in self.snapshots.items() if key.startswith(prefix)]

    async def start(self, workflow_type, argument, workflow_id, task_queue):
        self.started.append((workflow_type, argument, workflow_id, task_queue))

    async def query(self, workflow_id: str):
        return self.snapshots[workflow_id]

    async def signal(self, workflow_id: str, signal: str, argument: dict[str, Any]):
        self.signals.append((workflow_id, signal, argument))


class FakeQAAssets:
    def __init__(self):
        self.validations = []

    async def validate_manifest(self, tenant_id, pack_id, expected_sha256, expected_count):
        self.validations.append((tenant_id, pack_id, expected_sha256, expected_count))
        return [f"{tenant_id}/packs/{pack_id}/qa/asset-01.png"]

    async def review_assets(self, tenant_id, pack_id, expected_sha256, expected_count):
        keys = await self.validate_manifest(tenant_id, pack_id, expected_sha256, expected_count)
        return [{"id": "asset-01.png", "url": "https://objects.example/signed", "expires_in": 300} for _ in keys]


@pytest.fixture
def services():
    workflows = FakeWorkflows()
    qa = FakeQAAssets()
    app = create_app(workflows, FakeAuthenticator(), qa)
    with TestClient(app) as client:
        yield client, workflows, qa


def auth():
    return {"Authorization": "Bearer valid"}


def campaign_snapshot(status="awaiting_approval"):
    return {"tenant_id": "tenant-a", "campaign_id": "camp-1", "cycle_id": "cycle-1",
            "status": status, "amount_minor": 50000, "currency": "NGN", "channels": [],
            "started_at": "2026-10-28T00:00:00Z", "updated_at": "2026-10-28T00:00:00Z"}


def studio_snapshot(status="awaiting_qa"):
    return {"tenant_id": "tenant-a", "pack_id": "pack-1", "product_id": "product-1",
            "tier": "growth", "status": status, "assets": 1, "manifest_sha256": "a" * 64,
            "started_at": "2026-10-28T00:00:00Z", "updated_at": "2026-10-28T00:00:00Z"}


def studio_input(**changes):
    value = {"pack_id": "pack-1", "product_id": "product-1", "tier": "growth",
             "source_image": "tenant-a/uploads/product.png", "checkpoint": "reviewed.safetensors",
             "materials": "cotton", "dimensions": "30 cm", "price_text": "NGN 15000"}
    value.update(changes)
    return value


def test_health_and_ready_are_explicit_and_never_cached(services):
    client, _, _ = services
    health = client.get("/healthz")
    ready = client.get("/readyz")
    assert health.status_code == ready.status_code == 200
    assert health.headers["cache-control"] == "no-store"
    assert health.headers["x-content-type-options"] == "nosniff"


def test_authentication_and_reader_role_are_enforced():
    app = create_app(FakeWorkflows(), FakeAuthenticator(Claims("tenant-a", "analyst-1", frozenset({"analyst"}))), FakeQAAssets())
    with TestClient(app) as client:
        assert client.get("/v1/campaigns").status_code == 401
        response = client.get("/v1/campaigns", headers=auth())
        assert response.status_code == 200
        forbidden = client.post("/v1/campaigns", headers=auth(), json={
            "campaign_id": "camp-1", "cycle_id": "cycle-1", "brief": "Launch", "amount_minor": 100,
        })
        assert forbidden.status_code == 403


def test_campaign_queue_uses_only_authenticated_tenant_prefix(services):
    client, workflows, _ = services
    workflows.snapshots["campaign:tenant-a:camp-1"] = campaign_snapshot()
    workflows.snapshots["campaign:tenant-b:camp-2"] = campaign_snapshot() | {"tenant_id": "tenant-b", "campaign_id": "camp-2"}
    response = client.get("/v1/campaigns", headers=auth())
    assert response.status_code == 200
    assert workflows.prefixes == ["campaign:tenant-a:"]
    assert [item["campaign_id"] for item in response.json()["campaigns"]] == ["camp-1"]


def test_campaign_start_derives_tenant_and_rejects_injected_fields(services):
    client, workflows, _ = services
    body = {"campaign_id": "camp-1", "cycle_id": "cycle-1", "brief": "Launch", "amount_minor": 50000, "currency": "NGN"}
    response = client.post("/v1/campaigns", headers=auth(), json=body)
    assert response.status_code == 202
    workflow_type, argument, workflow_id, queue = workflows.started[0]
    assert workflow_type == "CampaignWorkflow"
    assert argument["tenant_id"] == "tenant-a"
    assert workflow_id == "campaign:tenant-a:camp-1"
    assert queue == "campaigns-v1"
    assert client.post("/v1/campaigns", headers=auth(), json=body | {"tenant_id": "tenant-b"}).status_code == 400


def test_campaign_approval_uses_oidc_actor_and_current_state(services):
    client, workflows, _ = services
    workflow_id = "campaign:tenant-a:camp-1"
    workflows.snapshots[workflow_id] = campaign_snapshot()
    response = client.post("/v1/campaigns/camp-1/approval", headers=auth(), json={"approval_id": "approval-1"})
    assert response.status_code == 202
    assert workflows.signals == [(workflow_id, "approve", {"approval_id": "approval-1", "approver": "operator-1"})]
    workflows.snapshots[workflow_id] = campaign_snapshot("completed")
    conflict = client.post("/v1/campaigns/camp-1/approval", headers=auth(), json={"approval_id": "approval-2"})
    assert conflict.status_code == 409
    assert len(workflows.signals) == 1


def test_snapshot_tenant_mismatch_is_not_disclosed(services):
    client, workflows, _ = services
    workflows.snapshots["campaign:tenant-a:camp-1"] = campaign_snapshot() | {"tenant_id": "tenant-b"}
    response = client.post("/v1/campaigns/camp-1/approval", headers=auth(), json={"approval_id": "approval-1"})
    assert response.status_code == 404


def test_studio_source_must_be_inside_authenticated_tenant(services):
    client, workflows, _ = services
    for source in ("tenant-b/uploads/product.png", "../tenant-a/product.png", "/tenant-a/product.png"):
        response = client.post("/v1/studio/packs", headers=auth(), json=studio_input(source_image=source))
        assert response.status_code == 400
    assert workflows.started == []
    accepted = client.post("/v1/studio/packs", headers=auth(), json=studio_input())
    assert accepted.status_code == 202
    assert workflows.started[0][2] == "studio:tenant-a:pack-1"


def test_studio_assets_are_manifest_bound_and_short_lived(services):
    client, workflows, qa = services
    workflows.snapshots["studio:tenant-a:pack-1"] = studio_snapshot()
    response = client.get("/v1/studio/packs/pack-1/assets", headers=auth())
    assert response.status_code == 200
    assert response.json()["manifest_sha256"] == "a" * 64
    assert response.json()["assets"][0]["expires_in"] == 300
    assert qa.validations == [("tenant-a", "pack-1", "a" * 64, 1)]


def test_studio_assets_are_only_available_during_current_human_qa(services):
    client, workflows, qa = services
    workflows.snapshots["studio:tenant-a:pack-1"] = studio_snapshot("delivered")
    response = client.get("/v1/studio/packs/pack-1/assets", headers=auth())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_state"
    assert qa.validations == []


def test_workflow_path_identifiers_use_the_same_bounded_contract_as_workflow_starts(services):
    client, workflows, qa = services
    invalid_id = "x" * 129
    campaign = client.post(f"/v1/campaigns/{invalid_id}/approval", headers=auth(), json={"approval_id": "approval-1"})
    assets = client.get(f"/v1/studio/packs/{invalid_id}/assets", headers=auth())
    review = client.post(f"/v1/studio/packs/{invalid_id}/review", headers=auth(), json={
        "decision": "approve", "reason": "", "manifest_sha256": "a" * 64,
    })
    assert campaign.status_code == assets.status_code == review.status_code == 400
    assert workflows.signals == []
    assert qa.validations == []


def test_studio_review_rejects_stale_manifest_and_requires_rejection_reason(services):
    client, workflows, _ = services
    workflow_id = "studio:tenant-a:pack-1"
    workflows.snapshots[workflow_id] = studio_snapshot()
    stale = client.post("/v1/studio/packs/pack-1/review", headers=auth(), json={
        "decision": "approve", "reason": "", "manifest_sha256": "b" * 64,
    })
    assert stale.status_code == 409
    missing_reason = client.post("/v1/studio/packs/pack-1/review", headers=auth(), json={
        "decision": "reject", "reason": "", "manifest_sha256": "a" * 64,
    })
    assert missing_reason.status_code == 400
    assert workflows.signals == []


def test_studio_review_revalidates_objects_and_records_oidc_reviewer(services):
    client, workflows, qa = services
    workflow_id = "studio:tenant-a:pack-1"
    workflows.snapshots[workflow_id] = studio_snapshot()
    response = client.post("/v1/studio/packs/pack-1/review", headers=auth(), json={
        "decision": "approve", "reason": "", "manifest_sha256": "a" * 64,
    })
    assert response.status_code == 202
    assert qa.validations == [("tenant-a", "pack-1", "a" * 64, 1)]
    assert workflows.signals == [(workflow_id, "review", {
        "decision": "approve", "reason": "", "reviewer": "operator-1", "manifest_sha256": "a" * 64,
    })]


def offer_snapshot(status="awaiting-review"):
    return {"tenant_id": "tenant-a", "job_id": "job-1", "offer": "08", "amount_minor": 50000,
            "currency": "NGN", "status": status, "approval_count": 0,
            "artifact": {"sha256": "d" * 64, "status": "draft"},
            "started_at": "2026-10-28T00:00:00Z", "updated_at": "2026-10-28T00:00:00Z"}


def test_offer_job_start_and_queue_are_tenant_scoped(services):
    client, workflows, _ = services
    workflows.snapshots["offer:tenant-a:job-1"] = offer_snapshot()
    body = {"job_id": "job-1", "offer": "08", "amount_minor": 50000, "currency": "NGN",
            "payload": {"cost_estimate_minor": 100, "job_ceiling_minor": 1000, "kind": "memo"}}
    response = client.post("/v1/offers/jobs", headers=auth(), json=body)
    assert response.status_code == 202
    workflow_type, argument, workflow_id, queue = workflows.started[0]
    assert workflow_type == "OfferWorkflow" and argument["tenant_id"] == "tenant-a"
    assert workflow_id == "offer:tenant-a:job-1" and queue == "offers-v1"
    listed = client.get("/v1/offers/jobs", headers=auth())
    assert listed.status_code == 200 and listed.json()["jobs"][0]["job_id"] == "job-1"
    assert client.post("/v1/offers/jobs", headers=auth(), json=body | {"tenant_id": "tenant-b"}).status_code == 400


def test_offer_review_binds_current_artifact_and_oidc_reviewer(services):
    client, workflows, _ = services
    workflow_id = "offer:tenant-a:job-1"
    workflows.snapshots[workflow_id] = offer_snapshot()
    stale = client.post("/v1/offers/jobs/job-1/review", headers=auth(),
                        json={"decision": "approved", "reason": "checked", "artifact_sha256": "e" * 64})
    assert stale.status_code == 409 and workflows.signals == []
    response = client.post("/v1/offers/jobs/job-1/review", headers=auth(),
                           json={"decision": "approved", "reason": "checked", "artifact_sha256": "d" * 64})
    assert response.status_code == 202
    signal = workflows.signals[0]
    assert signal[:2] == (workflow_id, "review")
    assert signal[2]["reviewer"] == "operator-1" and signal[2]["artifact_sha256"] == "d" * 64
    assert signal[2]["reviewed_at_unix"] > 0
    workflows.snapshots[workflow_id] = offer_snapshot("released")
    assert client.post("/v1/offers/jobs/job-1/review", headers=auth(),
                       json={"decision": "approved", "reason": "checked", "artifact_sha256": "d" * 64}).status_code == 409


def engagement_snapshot(status="awaiting-approval"):
    return {
        "tenant_id": "tenant-a", "engagement_id": "engagement-1", "offer": "27",
        "title": "Website planning", "status": status,
        "commercial_status": "source-only-unpriced", "delivery_mode": "plan-only",
        "charges_enabled": False, "external_actions_enabled": False,
        "plan": {"sha256": "a" * 64, "object_key": "tenant-a/engagements/27/engagement-1/plan-v1.json"},
        "document": {"schema_version": "volumeos.engagement-plan.v1"},
        "review": None, "started_at": "2026-11-08T00:00:00Z", "updated_at": "2026-11-08T00:00:00Z",
    }


def engagement_input(**changes):
    value = {
        "engagement_id": "engagement-1", "offer": "27", "title": "Website planning",
        "scope": "Define the redesign scope and approval gates.",
        "requested_outcome": "A reviewable delivery plan.",
    }
    value.update(changes)
    return value


def test_engagement_start_derives_tenant_and_never_accepts_commercial_fields(services):
    client, workflows, _ = services
    response = client.post("/v1/engagements", headers=auth(), json=engagement_input())
    assert response.status_code == 202
    assert response.json()["delivery_mode"] == "plan-only"
    workflow_type, argument, workflow_id, queue = workflows.started[0]
    assert workflow_type == "EngagementWorkflow"
    assert argument == engagement_input() | {"tenant_id": "tenant-a"}
    assert workflow_id == "engagement:tenant-a:engagement-1"
    assert queue == "engagements-v1"
    assert not ({"amount_minor", "currency", "price", "tiers"} & argument.keys())
    for injected in ({"tenant_id": "tenant-b"}, {"amount_minor": 1000}, {"currency": "NGN"}):
        rejected = client.post("/v1/engagements", headers=auth(), json=engagement_input() | injected)
        assert rejected.status_code == 400
    assert len(workflows.started) == 1


def test_engagement_start_rejects_non_volume_iii_offers_and_unbounded_input(services):
    client, workflows, _ = services
    for body in (
        engagement_input(offer="26"),
        engagement_input(offer="03"),
        engagement_input(title=""),
        engagement_input(scope=""),
        engagement_input(engagement_id="../escape"),
    ):
        assert client.post("/v1/engagements", headers=auth(), json=body).status_code == 400
    assert workflows.started == []


def test_engagement_list_and_get_use_only_authenticated_tenant_scope(services):
    client, workflows, _ = services
    workflow_id = "engagement:tenant-a:engagement-1"
    workflows.snapshots[workflow_id] = engagement_snapshot()
    workflows.snapshots["engagement:tenant-b:engagement-2"] = engagement_snapshot() | {
        "tenant_id": "tenant-b", "engagement_id": "engagement-2",
    }
    listed = client.get("/v1/engagements", headers=auth())
    assert listed.status_code == 200
    assert workflows.prefixes == ["engagement:tenant-a:"]
    assert [item["engagement_id"] for item in listed.json()["engagements"]] == ["engagement-1"]
    fetched = client.get("/v1/engagements/engagement-1", headers=auth())
    assert fetched.status_code == 200
    assert fetched.json()["document"]["schema_version"] == "volumeos.engagement-plan.v1"
    workflows.snapshots[workflow_id] = engagement_snapshot() | {"tenant_id": "tenant-b"}
    assert client.get("/v1/engagements/engagement-1", headers=auth()).status_code == 404


def test_engagement_review_rejects_stale_plan_and_invalid_state(services):
    client, workflows, _ = services
    workflow_id = "engagement:tenant-a:engagement-1"
    workflows.snapshots[workflow_id] = engagement_snapshot()
    stale = client.post("/v1/engagements/engagement-1/review", headers=auth(), json={
        "decision": "approved", "reason": "checked", "plan_sha256": "b" * 64,
    })
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_plan"
    workflows.snapshots[workflow_id] = engagement_snapshot("approved")
    terminal = client.post("/v1/engagements/engagement-1/review", headers=auth(), json={
        "decision": "approved", "reason": "checked", "plan_sha256": "a" * 64,
    })
    assert terminal.status_code == 409
    assert workflows.signals == []


def test_engagement_review_binds_plan_and_oidc_reviewer(services):
    client, workflows, _ = services
    workflow_id = "engagement:tenant-a:engagement-1"
    workflows.snapshots[workflow_id] = engagement_snapshot()
    response = client.post("/v1/engagements/engagement-1/review", headers=auth(), json={
        "decision": "approved", "reason": "Scope and controls checked.", "plan_sha256": "a" * 64,
    })
    assert response.status_code == 202
    assert len(workflows.signals) == 1
    signal = workflows.signals[0]
    assert signal[:2] == (workflow_id, "review")
    assert signal[2]["plan_sha256"] == "a" * 64
    assert signal[2]["reviewer"] == "operator-1"
    assert signal[2]["decision"] == "approved"
    assert signal[2]["reviewed_at_unix"] > 0


def test_engagement_rbac_allows_analyst_review_but_not_sales_mutation():
    analyst_app = create_app(
        FakeWorkflows(), FakeAuthenticator(Claims("tenant-a", "analyst-1", frozenset({"analyst"}))), FakeQAAssets(),
    )
    with TestClient(analyst_app) as client:
        workflow_id = "engagement:tenant-a:engagement-1"
        client.app.state.workflows.snapshots[workflow_id] = engagement_snapshot()
        review = client.post("/v1/engagements/engagement-1/review", headers=auth(), json={
            "decision": "rejected", "reason": "Scope needs revision.", "plan_sha256": "a" * 64,
        })
        assert review.status_code == 202
        assert client.app.state.workflows.signals[0][2]["reviewer"] == "analyst-1"
    sales_app = create_app(
        FakeWorkflows(), FakeAuthenticator(Claims("tenant-a", "sales-1", frozenset({"sales"}))), FakeQAAssets(),
    )
    with TestClient(sales_app) as client:
        assert client.get("/v1/engagements", headers=auth()).status_code == 200
        assert client.post("/v1/engagements", headers=auth(), json=engagement_input()).status_code == 403
        client.app.state.workflows.snapshots["engagement:tenant-a:engagement-1"] = engagement_snapshot()
        assert client.post("/v1/engagements/engagement-1/review", headers=auth(), json={
            "decision": "approved", "reason": "checked", "plan_sha256": "a" * 64,
        }).status_code == 403


def test_engagement_path_id_uses_bounded_contract(services):
    client, workflows, _ = services
    invalid_id = "x" * 129
    response = client.post(f"/v1/engagements/{invalid_id}/review", headers=auth(), json={
        "decision": "approved", "reason": "checked", "plan_sha256": "a" * 64,
    })
    assert response.status_code == 400
    assert workflows.signals == []


@pytest.mark.asyncio
async def test_temporal_readiness_uses_supported_health_rpc():
    health = AsyncMock(return_value=True)
    client = SimpleNamespace(service_client=SimpleNamespace(check_health=health))
    await TemporalWorkflows(client).ready()
    health.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_temporal_start_rejects_duplicate_workflow_ids_with_supported_enum():
    start = AsyncMock()
    client = SimpleNamespace(start_workflow=start)
    await TemporalWorkflows(client).start(
        "EngagementWorkflow", {"tenant_id": "tenant-a"},
        "engagement:tenant-a:plan-1", "engagements-v1",
    )
    assert start.await_args.kwargs["id_reuse_policy"] is WorkflowIDReusePolicy.REJECT_DUPLICATE
