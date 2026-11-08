from __future__ import annotations

import asyncio

import json
from datetime import datetime, timezone

import pytest
from temporalio.exceptions import ApplicationError

from common import OfferError, canonical, sha256
from engagements import (
    ENGAGEMENT_CONTROLS,
    ENGAGEMENT_OFFERS,
    EngagementJob,
    EngagementPlan,
    EngagementReview,
    EngagementWorkflow,
    build_engagement_plan,
    new_engagement_plan,
    validate_engagement,
)


def job(offer="27"):
    return EngagementJob(
        tenant_id="tenant-a",
        engagement_id="engagement-1",
        offer=offer,
        title="Launch planning",
        scope="Define the approved delivery scope and acceptance controls.",
        requested_outcome="A reviewable implementation plan.",
    )


def review(plan, decision="approved", **changes):
    value = {
        "plan_sha256": plan.sha256,
        "reviewer": "operator-1",
        "decision": decision,
        "reason": "Controls and scope checked.",
        "reviewed_at_unix": 1_793_000_000,
    }
    value.update(changes)
    return EngagementReview(**value)


def awaiting_workflow():
    generated = new_engagement_plan(job())
    instance = EngagementWorkflow()
    instance.tenant_id = "tenant-a"
    instance.engagement_id = "engagement-1"
    instance.offer = "27"
    instance.title = "Launch planning"
    instance.status = "awaiting-approval"
    instance.plan = generated.artifact
    instance.document = generated.document
    instance.started_at = instance.updated_at = "2026-11-08T00:00:00+00:00"
    return instance


def test_all_volume_iii_offers_have_server_defined_plan_controls():
    assert ENGAGEMENT_OFFERS == frozenset({"27", "28", "29", "30", "31", "32", "33", "34"})
    for offer in sorted(ENGAGEMENT_OFFERS):
        generated = new_engagement_plan(job(offer))
        assert generated.document["mandatory_controls"] == list(ENGAGEMENT_CONTROLS[offer])
        assert generated.document["commercial_status"] == "source-only-unpriced"
        assert generated.document["delivery_mode"] == "plan-only"
        assert generated.artifact.validated().offer == offer


def test_plan_generation_is_deterministic_and_contains_no_financial_terms():
    first = new_engagement_plan(job())
    second = new_engagement_plan(job())
    content = canonical(first.document).encode("utf-8")
    assert first == second
    assert first.artifact.sha256 == sha256(content)
    assert first.artifact.object_key == "tenant-a/engagements/27/engagement-1/plan-v1.json"
    assert not ({"amount_minor", "currency", "price", "tiers", "subscription_id"} & first.document.keys())
    assert "invoice, charge, reservation, settlement, or ledger posting" in first.document["prohibited_actions"]


def test_engagement_validation_rejects_unsupported_or_unbounded_jobs():
    validate_engagement(job())
    for invalid in (
        job("26"),
        EngagementJob("../tenant", "engagement-1", "27", "Title", "Scope"),
        EngagementJob("tenant-a", "../escape", "27", "Title", "Scope"),
        EngagementJob("tenant-a", "engagement-1", "27", "", "Scope"),
        EngagementJob("tenant-a", "engagement-1", "27", "Title", ""),
        EngagementJob("tenant-a", "engagement-1", "27", "x" * 161, "Scope"),
        EngagementJob("tenant-a", "engagement-1", "27", "Title", "x" * 10_001),
    ):
        with pytest.raises(ApplicationError):
            validate_engagement(invalid)


def test_plan_metadata_enforces_exact_tenant_key_and_digest():
    plan = new_engagement_plan(job()).artifact
    with pytest.raises(OfferError):
        EngagementPlan(**{**plan.__dict__, "object_key": "tenant-b/engagements/27/engagement-1/plan-v1.json"}).validated()
    with pytest.raises(OfferError):
        EngagementPlan(**{**plan.__dict__, "sha256": "g" * 64}).validated()
    with pytest.raises(OfferError):
        EngagementPlan(**{**plan.__dict__, "status": "released"}).validated()


@pytest.mark.asyncio
async def test_plan_activity_stores_only_digest_checked_tenant_objects(monkeypatch):
    class Store:
        def __init__(self):
            self.generated = None

        def put(self, generated):
            content = canonical(generated.document).encode("utf-8")
            assert sha256(content) == generated.artifact.sha256
            assert generated.artifact.object_key.startswith("tenant-a/engagements/27/engagement-1/")
            self.generated = generated

    store = Store()
    monkeypatch.setattr("engagements.plan_store", lambda: store)
    result = await build_engagement_plan(job())
    assert store.generated == result
    assert json.loads(canonical(result.document))["offer"] == "27"


def test_workflow_review_binds_exact_plan_and_oidc_actor():
    instance = awaiting_workflow()
    with pytest.raises(ApplicationError):
        instance.review(review(instance.plan, plan_sha256="0" * 64))
    instance.review(review(instance.plan))
    snapshot = instance.current_status()
    assert instance.status == "approved"
    assert snapshot["review"]["reviewer"] == "operator-1"
    assert snapshot["review"]["decision"] == "approved"
    assert snapshot["charges_enabled"] is False
    assert snapshot["external_actions_enabled"] is False
    with pytest.raises(ApplicationError):
        instance.review(review(instance.plan, reviewer="operator-2"))


def test_workflow_rejects_invalid_state_decision_and_missing_reason():
    instance = awaiting_workflow()
    with pytest.raises(ApplicationError):
        instance.review(review(instance.plan, reason=""))
    with pytest.raises(ApplicationError):
        instance.review(review(instance.plan, decision="released"))
    instance.review(review(instance.plan, decision="rejected", reason="Scope is not authorized."))
    assert instance.review_decision.decision == "rejected"
    instance.status = "approved"
    with pytest.raises(ApplicationError):
        instance.review(review(instance.plan))


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "rejected"])
async def test_run_applies_only_the_digest_bound_terminal_decision(monkeypatch, decision):
    instance = EngagementWorkflow()

    async def execute_activity(activity, activity_job, **_options):
        assert activity is build_engagement_plan
        assert activity_job == job()
        return new_engagement_plan(activity_job)

    async def wait_condition(predicate, **_options):
        instance.review(review(instance.plan, decision=decision))
        assert predicate()
        # Temporal returns None after a successful condition wait.
        return None

    monkeypatch.setattr("engagements.workflow.now", lambda: datetime(2026, 11, 8, tzinfo=timezone.utc))
    monkeypatch.setattr("engagements.workflow.execute_activity", execute_activity)
    monkeypatch.setattr("engagements.workflow.wait_condition", wait_condition)
    result = await instance.run(job())
    assert result["status"] == decision
    assert result["review"]["decision"] == decision
    assert result["plan"]["sha256"] == instance.plan.sha256
    assert result["charges_enabled"] is False
    assert result["external_actions_enabled"] is False


def test_s3_plan_store_rechecks_content_and_writes_tenant_manifest():
    class S3:
        def __init__(self):
            self.objects = {}

        def put_object(self, **kwargs):
            self.objects[kwargs["Key"]] = kwargs

    from engagements import S3EngagementPlanStore

    client = S3()
    store = S3EngagementPlanStore.__new__(S3EngagementPlanStore)
    store.bucket = "assets"
    store.client = client
    generated = new_engagement_plan(job())
    store.put(generated)
    assert client.objects[generated.artifact.object_key]["Body"] == canonical(generated.document).encode("utf-8")
    manifest = client.objects[generated.artifact.object_key + ".manifest.json"]
    assert manifest["Metadata"] == {"tenant-id": "tenant-a", "plan-sha256": generated.artifact.sha256}
    tampered = type(generated)(artifact=generated.artifact, document=generated.document | {"offer": "34"})
    with pytest.raises(OfferError):
        store.put(tampered)


@pytest.mark.asyncio
async def test_run_marks_only_actual_condition_timeout_as_expired(monkeypatch):
    instance = EngagementWorkflow()

    async def execute_activity(activity, activity_job, **_options):
        return new_engagement_plan(activity_job)

    async def wait_condition(_predicate, **_options):
        raise asyncio.TimeoutError

    monkeypatch.setattr("engagements.workflow.now", lambda: datetime(2026, 11, 8, tzinfo=timezone.utc))
    monkeypatch.setattr("engagements.workflow.execute_activity", execute_activity)
    monkeypatch.setattr("engagements.workflow.wait_condition", wait_condition)
    with pytest.raises(ApplicationError, match="approval expired"):
        await instance.run(job())
    assert instance.status == "approval-expired"
    assert instance.review_decision is None
