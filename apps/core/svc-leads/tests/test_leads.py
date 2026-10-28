import pytest

from leads import CadenceAction, LeadError, LeadInput, LeadService, MemoryLeadStore, ModelReview, normalize_phone

NOW = 1_700_000_000


def service(now=NOW):
    return LeadService(MemoryLeadStore(), clock=lambda: now)


def draft(**changes):
    values = dict(source="census", source_record_id="source-1", business_name="Adaeze Ventures",
                  phone="0803 123 4567", address="12 Admiralty Way", niche="retail", h3_r9="89a123456789abc",
                  consent_reference="consent-1", lineage_id="lineage-1")
    values.update(changes)
    return LeadInput(**values)


def review():
    return ModelReview("territory-model", "model-v1", "weights-v1", "approved", "reviewer-1", "gold-lineage-1",
                       {"territory": 1.0, "engagement": 0.5, "freshness": 0.2, "completeness": 0.1})


@pytest.mark.asyncio
async def test_ingestion_is_normalized_deduplicated_and_idempotent():
    svc = service()
    first = await svc.ingest("tenant-a", draft(), "agent-1")
    duplicate = await svc.ingest("tenant-a", draft(source="import", source_record_id="source-2", business_name="  ADAEZE   VENTURES ", phone="+2348031234567"), "agent-1")
    replay = await svc.ingest("tenant-a", draft(), "agent-1")
    assert first.lead_id == duplicate.lead_id == replay.lead_id
    assert first.phone == "+2348031234567"
    assert len(svc.store.leads) == 1 and len(svc.store.lineage) == 2


@pytest.mark.asyncio
async def test_same_phone_in_another_tenant_is_isolated():
    svc = service()
    one = await svc.ingest("tenant-a", draft(), "agent-1")
    two = await svc.ingest("tenant-b", draft(), "agent-1")
    assert one.lead_id != two.lead_id


@pytest.mark.asyncio
async def test_source_replay_cannot_change_content():
    svc = service(); await svc.ingest("tenant-a", draft(), "agent-1")
    with pytest.raises(LeadError, match="conflicts"):
        await svc.ingest("tenant-a", draft(business_name="Different Business"), "agent-1")


@pytest.mark.asyncio
async def test_score_requires_exact_human_review_and_gold_lineage():
    svc = service(); lead = await svc.ingest("tenant-a", draft(), "agent-1")
    gold = {"h3_r9": lead.h3_r9, "lineage_id": "gold-1", "territory": 0.8, "engagement": 0.5, "freshness": 1.0, "completeness": 1.0}
    result = await svc.score("tenant-a", lead.lead_id, review(), gold)
    assert 0 < result.score < 100 and result.model_version == "model-v1" and result.weight_version == "weights-v1"
    pending = ModelReview(**{**review().__dict__, "review_state": "pending"})
    with pytest.raises(LeadError, match="approved"):
        await svc.score("tenant-a", lead.lead_id, pending, gold)
    with pytest.raises(LeadError, match="H3"):
        await svc.score("tenant-a", lead.lead_id, review(), {**gold, "h3_r9": "89a123456789abd"})


@pytest.mark.asyncio
async def test_cadence_is_d0_d3_d7_d14_then_recycle_and_requires_approval():
    svc = service(); lead = await svc.ingest("tenant-a", draft(), "agent-1")
    for step, day in (("D0", 0), ("D3", 3), ("D7", 7), ("D14", 14)):
        svc.clock = lambda day=day: NOW + day * 86400
        due = await svc.due_cadence("tenant-a", lead.lead_id)
        assert due.step == step and due.status == "requires-human-approval"
        await svc.record_cadence("tenant-a", lead.lead_id, step, f"approval-{step}", "approver-1")
    svc.clock = lambda: NOW + 30 * 86400
    assert (await svc.due_cadence("tenant-a", lead.lead_id)).step == "RECYCLE"
    await svc.record_cadence("tenant-a", lead.lead_id, "RECYCLE", "approval-recycle", "approver-1")
    assert (await svc.store.get("tenant-a", lead.lead_id)).stage == "recycled"
    assert await svc.due_cadence("tenant-a", lead.lead_id) is None


class Consent:
    def __init__(self, granted): self.value = granted
    async def granted(self, tenant_id, reference, purpose):
        return self.value and tenant_id == "tenant-a" and reference == "consent-1" and purpose == "lead-conversion"


@pytest.mark.asyncio
async def test_conversion_follows_state_machine_and_consent():
    svc = service(); svc.consent = Consent(True)
    lead = await svc.ingest("tenant-a", draft(), "agent-1")
    with pytest.raises(LeadError, match="opportunity"):
        await svc.convert("tenant-a", lead.lead_id, "agent-1")
    for target in ("contacted", "qualified", "opportunity"):
        await svc.transition("tenant-a", lead.lead_id, target, "agent-1")
    converted = await svc.convert("tenant-a", lead.lead_id, "agent-1")
    assert converted.opportunity_id.startswith("opportunity_")
    assert await svc.convert("tenant-a", lead.lead_id, "agent-1") == converted
    assert (await svc.store.get("tenant-a", lead.lead_id)).stage == "converted"


@pytest.mark.asyncio
async def test_conversion_fails_without_consent():
    svc = service(); svc.consent = Consent(False)
    lead = await svc.ingest("tenant-a", draft(), "agent-1")
    for target in ("contacted", "qualified", "opportunity"):
        await svc.transition("tenant-a", lead.lead_id, target, "agent-1")
    with pytest.raises(LeadError, match="consent"):
        await svc.convert("tenant-a", lead.lead_id, "agent-1")


@pytest.mark.asyncio
async def test_reviewed_gold_gateway_is_the_api_scoring_path():
    class Gold:
        async def review(self, tenant_id, model_version, weight_version):
            return ModelReview("model", model_version, weight_version, "approved", "reviewer-1", "gold-1",
                               {"territory": 1.0, "engagement": 1.0, "freshness": 1.0, "completeness": 1.0})
        async def features(self, tenant_id, lead_id, h3_r9, model_version, weight_version):
            return {"h3_r9": h3_r9, "lineage_id": "gold-1", "territory": 0.5, "engagement": 0.5, "freshness": 0.5, "completeness": 0.5}
    svc = service(); svc.gold = Gold()
    lead = await svc.ingest("tenant-a", draft(), "agent-1")
    result = await svc.score_reviewed("tenant-a", lead.lead_id, "model-v2", "weights-v2")
    assert result.model_version == "model-v2" and result.weight_version == "weights-v2"


def test_phone_rejects_invalid_nigerian_numbers():
    assert normalize_phone("07031234567") == "+2347031234567"
    with pytest.raises(LeadError): normalize_phone("+14155550123")
