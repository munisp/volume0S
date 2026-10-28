import hashlib
import hmac
import json

import pytest

from conversations import ConversationError, ConversationService, MemoryConversationStore


KEY = b"x" * 32


def webhook(message_id="wamid.1", recipient="2348031234567", text="Hello"):
    return json.dumps({"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "123456"},
        "messages": [{"id": message_id, "from": recipient, "type": "text",
                      "text": {"body": text}, "timestamp": "1700000000"}],
        "contacts": [{"profile": {"name": "Test Customer"}}],
    }}]}]}).encode()


def signature(body):
    return "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()


class AI:
    def __init__(self, confidence=0.9): self.confidence = confidence; self.calls = 0
    async def complete(self, tenant_id, prompt, model):
        self.calls += 1
        result = {"output_text": "We can help with that."}
        if self.confidence is not None: result["confidence"] = self.confidence
        return result


class Sender:
    def __init__(self): self.calls = 0
    async def send(self, conversation, draft):
        self.calls += 1
        return {"messages": [{"id": "wamid.outbound"}]}


class Booking:
    def __init__(self): self.holds = 0; self.confirms = 0
    async def hold(self, tenant_id, conversation_id, slot_id, idempotency_key):
        self.holds += 1; return {"hold_id": "hold-1"}
    async def confirm(self, tenant_id, hold_id, idempotency_key):
        self.confirms += 1; return {"hold_id": hold_id, "status": "confirmed"}


def fixture(confidence=0.9):
    store = MemoryConversationStore(); store.channels[("tenant-a", "123456")] = True
    ai, sender, booking = AI(confidence), Sender(), Booking()
    service = ConversationService(store, ai, sender, booking, KEY, clock=lambda: 1_700_000_001)
    digest = service.subject_digest("2348031234567")
    store.consents.add(("tenant-a", digest, "service-delivery"))
    store.consents.add(("tenant-a", digest, "ai-processing"))
    return store, service, ai, sender, booking


@pytest.mark.asyncio
async def test_signed_intake_replay_exact_approval_and_send_replay():
    store, service, ai, sender, _ = fixture(); body = webhook()
    created = await service.ingest("tenant-a", body, signature(body), "test-secret", "model-v1")
    assert created["status"] == "requires-review" and ai.calls == 1
    replay = await service.ingest("tenant-a", body, signature(body), "test-secret", "model-v1")
    assert replay["status"] == "replayed" and replay["draft_sha256"] == created["draft_sha256"] and ai.calls == 1
    with pytest.raises(ConversationError, match="human approval"):
        await service.send("tenant-a", created["conversation_id"], created["draft_sha256"])
    await service.review("tenant-a", created["conversation_id"], created["draft_sha256"],
                         "reviewer-1", "approved", "response checked")
    sent = await service.send("tenant-a", created["conversation_id"], created["draft_sha256"])
    assert sent["status"] == "sent" and sender.calls == 1
    replayed = await service.send("tenant-a", created["conversation_id"], created["draft_sha256"])
    assert replayed["status"] == "replayed" and sender.calls == 1


@pytest.mark.asyncio
async def test_consent_suppression_and_channel_binding_fail_closed():
    store, service, ai, _, _ = fixture()
    store.consents.clear(); body = webhook()
    result = await service.ingest("tenant-a", body, signature(body), "test-secret", "model-v1")
    assert result["status"] == "suppressed" and ai.calls == 0
    with pytest.raises(ConversationError, match="not registered"):
        await service.ingest("tenant-b", body, signature(body), "test-secret", "model-v1")
    with pytest.raises(ConversationError, match="mismatch"):
        await service.ingest("tenant-a", body, "sha256=" + "0" * 64, "test-secret", "model-v1")


@pytest.mark.asyncio
async def test_suppression_blocks_an_already_approved_reply():
    _, service, _, sender, _ = fixture(); body = webhook()
    created = await service.ingest("tenant-a", body, signature(body), "test-secret", "model-v1")
    await service.review("tenant-a", created["conversation_id"], created["draft_sha256"],
                         "reviewer-1", "approved", "checked")
    await service.suppress("tenant-a", created["conversation_id"], "privacy-1", "customer withdrew")
    with pytest.raises(ConversationError, match="suppression"):
        await service.send("tenant-a", created["conversation_id"], created["draft_sha256"])
    assert sender.calls == 0


@pytest.mark.asyncio
async def test_model_confidence_is_required_and_low_confidence_escalates():
    _, service, _, _, _ = fixture(None); body = webhook()
    with pytest.raises(ConversationError, match="numeric confidence"):
        await service.ingest("tenant-a", body, signature(body), "test-secret", "model-v1")
    store, service, _, _, _ = fixture(0.2); body = webhook("wamid.low")
    result = await service.ingest("tenant-a", body, signature(body), "test-secret", "model-v1")
    assert result["escalation_reason"] == "low-confidence"


@pytest.mark.asyncio
async def test_booking_is_durable_and_idempotent():
    _, service, _, _, booking = fixture(); body = webhook()
    created = await service.ingest("tenant-a", body, signature(body), "test-secret", "model-v1")
    first = await service.book("tenant-a", created["conversation_id"], "slot-1")
    second = await service.book("tenant-a", created["conversation_id"], "slot-1")
    assert first == second and booking.holds == 1 and booking.confirms == 1
