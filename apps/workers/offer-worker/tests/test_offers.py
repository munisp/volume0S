import hashlib
import hmac
import json
from datetime import date

import pytest

from books import ExtractedDocument, accounting_csv
from common import Approval, Artifact, BudgetGuard, OfferError, release_artifact, sha256
from compliance import Licence, PortalCheck, paperwork, renewal_calendar
from front_desk import AgentDraft, WhatsAppCloudClient, approved_reply, book, draft_artifact, escalation_reason, inbound_message
from funding import FundingJob, new_artifact, release_funding
from scorecards import Transcript, accent_qa_sample, build_report, rubric_score


def approval(artifact, reviewer="analyst-1", decision="approved"):
    return Approval(artifact.sha256, reviewer, decision, "reviewed", 1_700_000_000)


def transcript():
    return Transcript("tenant-a", "call-1", "tenant-a/calls/call-1/source.wav", "a" * 64, "whisperx-v1", "en",
                      [{"start": 0.0, "end": 2.0, "text": "Welcome", "labels": ["greeting"]},
                       {"start": 2.0, "end": 4.0, "text": "How may I help?", "labels": ["discovery"]}], 0.1)


def test_scorecard_rubric_evidence_and_reviewed_report():
    value = transcript(); score = rubric_score(value, "rubric-v1", {"greeting": 0.4, "discovery": 0.6}, {"greeting": 5, "discovery": 4})
    assert score["weighted_score"] == 4.4
    report = build_report(value, score, qa_reviewer="qa-1", qa_reason="accent sample checked")
    assert report.offer == "03" and report.status == "draft"
    with pytest.raises(OfferError, match="exact transcript"):
        build_report(value, {**score, "transcript_sha256": "bad"})


def test_scorecard_requires_qa_for_high_wer():
    value = Transcript(**{**transcript().__dict__, "wer": 0.5})
    score = rubric_score(value, "rubric-v1", {"greeting": 0.4, "discovery": 0.6}, {"greeting": 5, "discovery": 4})
    with pytest.raises(OfferError, match="human QA"):
        build_report(value, score)


def document(confidence=1.0):
    fields = {"document_number": "INV-1", "issue_date": "2026-01-01", "seller_tin": "12345678-0001", "buyer_tin": "87654321-0001",
              "currency": "NGN", "net_minor": 10000, "tax_minor": 750, "total_minor": 10750}
    return ExtractedDocument("tenant-a", "doc-1", "b" * 64, "docling-v1", "invoice", fields, {key: confidence for key in fields})


def test_docling_confidence_exception_requires_review_and_export_reconciles():
    low = document(0.8)
    assert low.exceptions() == sorted(low.fields)
    with pytest.raises(OfferError, match="human extraction review"):
        accounting_csv(low, "zoho")
    artifact = accounting_csv(low, "zoho", reviewer="reviewer-1", review_reason="verified against source")
    assert artifact.offer == "05" and artifact.object_key.endswith("zoho-v1.csv")
    with pytest.raises(OfferError, match="reconcile"):
        ExtractedDocument(**{**document().__dict__, "fields": {**document().fields, "total_minor": 1}}).validated()


def test_renewal_calendar_and_evidence_bound_paperwork():
    licence = Licence("tenant-a", "licence-1", "cac", "RC12345", "2026-04-01", "https://registry.example/renew")
    assert renewal_calendar(licence, date(2026, 3, 15))[0]["status"] == "renewal-due"
    check = PortalCheck("tenant-a", "licence-1", "2026-03-15T00:00:00Z", "renewal-open", "c" * 64, "tenant-a/registry/licence-1/check.png")
    artifact = paperwork(licence, check, {"business_name": "Test Business"})
    assert artifact.offer == "07"
    with pytest.raises(OfferError, match="another licence"):
        paperwork(licence, PortalCheck(**{**check.__dict__, "licence_id": "other", "screenshot_object_key": "tenant-a/registry/other/check.png"}), {"business_name": "Test"})


def test_funding_versions_require_parent_and_two_analysts():
    job = FundingJob("tenant-a", "funding-1", "memo", "memo-v1", 10000)
    first = new_artifact(job, b"memo-v1", 1)
    second = new_artifact(job, b"memo-v2", 2, first)
    assert second.parent_sha256 == first.sha256
    with pytest.raises(OfferError, match="version one"):
        new_artifact(job, b"bad", 2)
    with pytest.raises(OfferError, match="2 distinct"):
        release_funding(second, [approval(second)])
    released = release_funding(second, [approval(second), approval(second, "analyst-2")])
    assert released.status == "released"
    with pytest.raises(OfferError, match="exact artifact"):
        approval(second, "analyst-3").validated(first)


def test_whatsapp_signature_and_payload_validation():
    body = json.dumps({"entry": [{"changes": [{"value": {"messages": [{"id": "wamid.1", "from": "2348031234567", "type": "text", "text": {"body": "Hello"}, "timestamp": "1700000000"}], "contacts": [{"profile": {"name": "Test"}}]}}]}]}).encode()
    signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    message = inbound_message(body, signature, "secret")
    assert message["message_id"] == "wamid.1"
    with pytest.raises(OfferError, match="mismatch"):
        inbound_message(body, "sha256=" + "0" * 64, "secret")


def test_front_desk_escalation_and_exact_reply_approval():
    draft = AgentDraft("tenant-a", "conversation-1", "wamid.1", "I can help", 0.8, "model-v1")
    artifact = draft_artifact(draft)
    with pytest.raises(OfferError, match="human approval"):
        approved_reply(draft, [])
    assert approved_reply(draft, [approval(artifact)]) == draft
    assert escalation_reason("I need a manager", 0.9) == "keyword:manager"
    assert escalation_reason("Hello", 0.2) == "low-confidence"


class Booking:
    async def hold(self, tenant_id, conversation_id, slot_id, idempotency_key): return {"hold_id": "hold-1"}
    async def confirm(self, tenant_id, hold_id, idempotency_key): return {"hold_id": hold_id, "status": "confirmed"}


@pytest.mark.asyncio
async def test_booking_is_hold_then_confirm():
    assert (await book(Booking(), "tenant-a", "conversation-1", "slot-1"))["status"] == "confirmed"


class Costs:
    def __init__(self): self.values = {}; self.reservations = []
    async def spent_minor(self, tenant_id, job_id): return self.values.get((tenant_id, job_id), 0)
    async def reserve(self, tenant_id, job_id, amount_minor, idempotency_key): self.reservations.append((tenant_id, job_id, amount_minor, idempotency_key))


@pytest.mark.asyncio
async def test_budget_guard_stops_job_and_tenant_overruns():
    costs = Costs(); guard = BudgetGuard(costs, 1000)
    await guard.before_provider_call("tenant-a", "job-1", 400, 500)
    costs.values[("tenant-a", "job-1")] = 200
    with pytest.raises(OfferError, match="denied"):
        await guard.before_provider_call("tenant-a", "job-1", 400, 500)
    costs.values[("tenant-a", None)] = 900
    with pytest.raises(OfferError, match="denied"):
        await guard.before_provider_call("tenant-a", "job-2", 200, 500)
