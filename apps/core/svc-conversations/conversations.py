"""Offer 04 conversation domain with signed intake and human-approved effects."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
import math
import re
import time
from typing import Any, Protocol

SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
PHONE = re.compile(r"^234[789][01]\d{8}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ESCALATION_TERMS = {"refund", "lawyer", "complaint", "angry", "fraud", "emergency", "human", "manager"}


class ConversationError(ValueError):
    pass


def identifier(value: str, label: str = "identifier") -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ConversationError(f"invalid {label}")
    return value


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(value: bytes | str) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def verify_whatsapp_signature(raw_body: bytes, signature: str, app_secret: str) -> None:
    if not app_secret or not signature.startswith("sha256="):
        raise ConversationError("WhatsApp signature is required")
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature[7:], expected):
        raise ConversationError("WhatsApp signature mismatch")


def inbound_message(raw_body: bytes, signature: str, app_secret: str) -> dict[str, Any]:
    verify_whatsapp_signature(raw_body, signature, app_secret)
    try:
        document = json.loads(raw_body)
        value = document["entry"][0]["changes"][0]["value"]
        message = value["messages"][0]
        contact = value["contacts"][0]
        result = {
            "message_id": message["id"], "from": message["from"], "type": message["type"],
            "text": message.get("text", {}).get("body", ""), "timestamp": int(message["timestamp"]),
            "contact_name": contact.get("profile", {}).get("name", ""),
            "phone_number_id": value["metadata"]["phone_number_id"],
        }
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ConversationError("unsupported WhatsApp webhook payload") from exc
    if (not result["message_id"] or len(result["message_id"]) > 128 or not PHONE.fullmatch(result["from"])
            or result["type"] != "text" or not result["text"].strip() or len(result["text"]) > 4096
            or not isinstance(result["phone_number_id"], str) or not re.fullmatch(r"\d{1,32}", result["phone_number_id"])
            or len(result["contact_name"]) > 200):
        raise ConversationError("invalid WhatsApp message identity, phone, channel, or body")
    return result


@dataclass(frozen=True)
class Conversation:
    tenant_id: str
    conversation_id: str
    phone_number_id: str
    recipient: str
    contact_name: str
    subject_digest: str
    status: str
    created_at_unix: int

    def validated(self) -> "Conversation":
        identifier(self.tenant_id, "tenant"); identifier(self.conversation_id, "conversation")
        if (not re.fullmatch(r"\d{1,32}", self.phone_number_id) or not PHONE.fullmatch(self.recipient)
                or not SHA256.fullmatch(self.subject_digest) or self.status not in {"active", "suppressed"}
                or len(self.contact_name) > 200 or self.created_at_unix <= 0):
            raise ConversationError("invalid conversation scope or status")
        return self


@dataclass(frozen=True)
class Message:
    tenant_id: str
    conversation_id: str
    message_id: str
    direction: str
    recipient: str
    body: str
    fingerprint: str
    created_at_unix: int
    provider_message_id: str | None = None
    draft_sha256: str | None = None

    def validated(self) -> "Message":
        identifier(self.tenant_id, "tenant"); identifier(self.conversation_id, "conversation")
        if (not self.message_id or len(self.message_id) > 128 or self.direction not in {"inbound", "outbound"}
                or not PHONE.fullmatch(self.recipient) or not self.body.strip() or len(self.body) > 4096
                or not SHA256.fullmatch(self.fingerprint) or self.created_at_unix <= 0):
            raise ConversationError("invalid conversation message")
        if self.direction == "outbound" and (not self.provider_message_id or not self.draft_sha256):
            raise ConversationError("outbound message must bind a provider ID and approved draft")
        return self


@dataclass(frozen=True)
class Draft:
    tenant_id: str
    conversation_id: str
    inbound_message_id: str
    response_text: str
    confidence: float
    model_version: str
    escalation_reason: str | None
    status: str = "requires-review"
    created_at_unix: int = 1

    def validated(self) -> "Draft":
        identifier(self.tenant_id, "tenant"); identifier(self.conversation_id, "conversation")
        identifier(self.model_version, "model")
        if (not self.inbound_message_id or not self.response_text.strip() or len(self.response_text) > 4096
                or isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float))
                or not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1
                or self.status not in {"requires-review", "approved", "rejected", "sent"}
                or self.created_at_unix <= 0):
            raise ConversationError("invalid agent draft, confidence, or review state")
        return self

    @property
    def digest(self) -> str:
        self.validated()
        return sha256(canonical({
            "tenant_id": self.tenant_id, "conversation_id": self.conversation_id,
            "inbound_message_id": self.inbound_message_id, "response_text": self.response_text,
            "confidence": self.confidence, "model_version": self.model_version,
            "escalation_reason": self.escalation_reason,
        }))


@dataclass(frozen=True)
class Approval:
    tenant_id: str
    conversation_id: str
    draft_sha256: str
    reviewer: str
    decision: str
    reason: str
    reviewed_at_unix: int

    def validated(self) -> "Approval":
        identifier(self.tenant_id, "tenant"); identifier(self.conversation_id, "conversation")
        identifier(self.reviewer, "reviewer")
        if (not SHA256.fullmatch(self.draft_sha256) or self.decision not in {"approved", "rejected"}
                or not self.reason.strip() or len(self.reason) > 1000 or self.reviewed_at_unix <= 0):
            raise ConversationError("approval must bind the exact draft and include a reason")
        return self


def escalation_reason(text: str, confidence: float, threshold: float = 0.7) -> str | None:
    terms = {term for term in ESCALATION_TERMS if re.search(rf"\b{re.escape(term)}\b", text, re.I)}
    if terms:
        return "keyword:" + ",".join(sorted(terms))
    return "low-confidence" if confidence < threshold else None


class AIClient(Protocol):
    async def complete(self, tenant_id: str, prompt: str, model: str) -> dict[str, Any]: ...


class WhatsAppSender(Protocol):
    async def send(self, conversation: Conversation, draft: Draft) -> dict[str, Any]: ...


class BookingAdapter(Protocol):
    async def hold(self, tenant_id: str, conversation_id: str, slot_id: str, idempotency_key: str) -> dict[str, Any]: ...
    async def confirm(self, tenant_id: str, hold_id: str, idempotency_key: str) -> dict[str, Any]: ...


class ConversationService:
    def __init__(self, store, ai: AIClient, sender: WhatsAppSender, booking: BookingAdapter,
                 subject_key: bytes, clock=time.time):
        if len(subject_key) < 32:
            raise ValueError("SUBJECT_DIGEST_KEY must contain at least 32 bytes")
        self.store, self.ai, self.sender, self.booking = store, ai, sender, booking
        self.subject_key, self.clock = subject_key, clock

    def subject_digest(self, recipient: str) -> str:
        if not PHONE.fullmatch(recipient):
            raise ConversationError("invalid WhatsApp subject")
        subject = f"whatsapp:+{recipient}"
        return hmac.new(self.subject_key, subject.encode(), hashlib.sha256).hexdigest()

    async def ingest(self, tenant_id: str, raw_body: bytes, signature: str, app_secret: str,
                     model_version: str) -> dict[str, Any]:
        identifier(tenant_id, "tenant"); identifier(model_version, "model")
        inbound = inbound_message(raw_body, signature, app_secret)
        if not await self.store.channel_matches(tenant_id, inbound["phone_number_id"]):
            raise ConversationError("WhatsApp channel is not registered to this tenant")
        conversation_id = "conv-" + sha256(f"{tenant_id}|{inbound['phone_number_id']}|{inbound['from']}")[:32]
        conversation = Conversation(tenant_id, conversation_id, inbound["phone_number_id"], inbound["from"],
                                    inbound["contact_name"], self.subject_digest(inbound["from"]), "active", int(self.clock())).validated()
        message = Message(tenant_id, conversation_id, inbound["message_id"], "inbound", inbound["from"],
                          inbound["text"], sha256(canonical(inbound)), int(self.clock())).validated()
        created = await self.store.record_inbound(conversation, message)
        if not created:
            draft = await self.store.draft_for_message(tenant_id, conversation_id, inbound["message_id"])
            return {"status": "replayed", "conversation_id": conversation_id,
                    "draft_sha256": draft.digest if draft else None}
        if not await self.store.allowed(tenant_id, conversation.recipient, conversation.subject_digest):
            await self.store.set_status(tenant_id, conversation_id, "suppressed")
            return {"status": "suppressed", "conversation_id": conversation_id, "draft_sha256": None}
        result = await self.ai.complete(tenant_id, inbound["text"], model_version)
        confidence = result.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ConversationError("redacted AI response must include numeric confidence")
        confidence = float(confidence)
        response = str(result.get("output_text", "")).strip()
        reason = escalation_reason(inbound["text"] + "\n" + response, confidence)
        draft = Draft(tenant_id, conversation_id, inbound["message_id"], response, confidence,
                      model_version, reason, created_at_unix=int(self.clock())).validated()
        await self.store.save_draft(draft)
        return {"status": "requires-review", "conversation_id": conversation_id, "draft_sha256": draft.digest,
                "escalation_reason": reason}

    async def queue(self, tenant_id: str) -> list[dict[str, Any]]:
        identifier(tenant_id, "tenant")
        return await self.store.queue(tenant_id)

    async def review(self, tenant_id: str, conversation_id: str, draft_sha256: str, reviewer: str,
                     decision: str, reason: str) -> Draft:
        draft = await self._draft(tenant_id, conversation_id, draft_sha256)
        if draft.status in {"sent", "rejected"}:
            raise ConversationError("sent or rejected draft cannot be reviewed again")
        approval = Approval(tenant_id, conversation_id, draft_sha256, reviewer, decision, reason, int(self.clock())).validated()
        await self.store.review(draft, approval)
        return await self._draft(tenant_id, conversation_id, draft_sha256)

    async def send(self, tenant_id: str, conversation_id: str, draft_sha256: str) -> dict[str, Any]:
        conversation = await self._conversation(tenant_id, conversation_id)
        draft = await self._draft(tenant_id, conversation_id, draft_sha256)
        if conversation.status != "active" or not await self.store.allowed(tenant_id, conversation.recipient, conversation.subject_digest):
            raise ConversationError("WhatsApp opt-in/suppression check failed")
        existing = await self.store.outbound_for_draft(tenant_id, conversation_id, draft_sha256)
        if existing:
            return {"status": "replayed", "provider_message_id": existing.provider_message_id}
        approvals = await self.store.approvals(tenant_id, conversation_id, draft_sha256)
        if any(item.decision == "rejected" for item in approvals) or not any(item.decision == "approved" for item in approvals):
            raise ConversationError("human approval is required for the exact front-desk reply")
        result = await self.sender.send(conversation, draft)
        try:
            provider_id = result["messages"][0]["id"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ConversationError("WhatsApp did not accept the approved message") from exc
        outbound = Message(tenant_id, conversation_id, f"outbound:{draft_sha256[:32]}", "outbound",
                           conversation.recipient, draft.response_text, draft_sha256, int(self.clock()),
                           str(provider_id), draft_sha256).validated()
        await self.store.record_outbound(outbound)
        return {"status": "sent", "provider_message_id": provider_id}

    async def book(self, tenant_id: str, conversation_id: str, slot_id: str) -> dict[str, Any]:
        identifier(slot_id, "booking slot")
        conversation = await self._conversation(tenant_id, conversation_id)
        if not await self.store.allowed(tenant_id, conversation.recipient, conversation.subject_digest):
            raise ConversationError("booking requires current service-delivery consent")
        existing = await self.store.booking(tenant_id, conversation_id, slot_id)
        if existing:
            return existing
        hold_key = f"booking-hold:{tenant_id}:{conversation_id}:{slot_id}"
        hold = await self.booking.hold(tenant_id, conversation_id, slot_id, hold_key)
        hold_id = str(hold.get("hold_id", "")); identifier(hold_id, "booking hold")
        confirmed = await self.booking.confirm(tenant_id, hold_id, f"booking-confirm:{tenant_id}:{conversation_id}:{hold_id}")
        if confirmed.get("status") != "confirmed" or confirmed.get("hold_id") != hold_id:
            raise ConversationError("booking adapter did not confirm the exact hold")
        document = {**confirmed, "tenant_id": tenant_id, "conversation_id": conversation_id, "slot_id": slot_id}
        await self.store.save_booking(tenant_id, conversation_id, slot_id, document)
        return document

    async def suppress(self, tenant_id: str, conversation_id: str, actor: str, reason: str) -> None:
        identifier(actor, "actor")
        if not reason.strip():
            raise ConversationError("suppression reason is required")
        conversation = await self._conversation(tenant_id, conversation_id)
        await self.store.suppress(conversation, actor, reason, int(self.clock()))

    async def _conversation(self, tenant_id: str, conversation_id: str) -> Conversation:
        value = await self.store.conversation(tenant_id, conversation_id)
        if value is None:
            raise ConversationError("conversation not found")
        return value

    async def _draft(self, tenant_id: str, conversation_id: str, digest: str) -> Draft:
        value = await self.store.draft(tenant_id, conversation_id, digest)
        if value is None:
            raise ConversationError("draft not found")
        return value


class MemoryConversationStore:
    """Test-only store; production construction selects PostgreSQL/RLS."""
    def __init__(self):
        self.channels: dict[tuple[str, str], bool] = {}
        self.conversations: dict[tuple[str, str], Conversation] = {}
        self.messages: dict[tuple[str, str], Message] = {}
        self.drafts: dict[tuple[str, str], Draft] = {}
        self.reviews: list[Approval] = []
        self.bookings: dict[tuple[str, str, str], dict] = {}
        self.consents: set[tuple[str, str, str]] = set()
        self.suppressions: set[tuple[str, str]] = set()
        self.lock = asyncio.Lock()

    async def ready(self):
        return None

    async def channel_matches(self, tenant_id, phone_number_id):
        return self.channels.get((tenant_id, phone_number_id), False)

    async def queue(self, tenant_id):
        result = []
        for conversation in self.conversations.values():
            if conversation.tenant_id != tenant_id:
                continue
            drafts = [{**asdict(draft), "draft_sha256": draft.digest} for draft in self.drafts.values()
                      if draft.tenant_id == tenant_id and draft.conversation_id == conversation.conversation_id]
            result.append({"conversation": asdict(conversation), "drafts": drafts})
        return sorted(result, key=lambda item: item["conversation"]["created_at_unix"], reverse=True)[:100]

    async def record_inbound(self, conversation, message):
        async with self.lock:
            key = (conversation.tenant_id, message.message_id)
            existing = self.messages.get(key)
            if existing:
                if existing.fingerprint != message.fingerprint or existing.conversation_id != message.conversation_id:
                    raise ConversationError("webhook replay conflicts with original content")
                return False
            self.conversations[(conversation.tenant_id, conversation.conversation_id)] = conversation
            self.messages[key] = message
            return True

    async def set_status(self, tenant_id, conversation_id, status):
        current = await self.conversation(tenant_id, conversation_id)
        self.conversations[(tenant_id, conversation_id)] = Conversation(**{**asdict(current), "status": status})

    async def allowed(self, tenant_id, recipient, subject_digest):
        return ((tenant_id, recipient) not in self.suppressions
                and (tenant_id, subject_digest, "service-delivery") in self.consents
                and (tenant_id, subject_digest, "ai-processing") in self.consents)

    async def conversation(self, tenant_id, conversation_id):
        return self.conversations.get((tenant_id, conversation_id))

    async def save_draft(self, draft):
        self.drafts[(draft.tenant_id, draft.digest)] = draft

    async def draft_for_message(self, tenant_id, conversation_id, message_id):
        return next((item for item in self.drafts.values() if item.tenant_id == tenant_id
                     and item.conversation_id == conversation_id and item.inbound_message_id == message_id), None)

    async def draft(self, tenant_id, conversation_id, digest):
        value = self.drafts.get((tenant_id, digest))
        return value if value and value.conversation_id == conversation_id else None

    async def review(self, draft, approval):
        async with self.lock:
            if any(item.draft_sha256 == approval.draft_sha256 and item.reviewer == approval.reviewer for item in self.reviews):
                raise ConversationError("reviewer already decided on this draft")
            self.reviews.append(approval)
            self.drafts[(draft.tenant_id, draft.digest)] = Draft(**{**asdict(draft), "status": approval.decision})

    async def approvals(self, tenant_id, conversation_id, digest):
        return [item for item in self.reviews if item.tenant_id == tenant_id
                and item.conversation_id == conversation_id and item.draft_sha256 == digest]

    async def outbound_for_draft(self, tenant_id, conversation_id, digest):
        return next((item for item in self.messages.values() if item.tenant_id == tenant_id
                     and item.conversation_id == conversation_id and item.draft_sha256 == digest), None)

    async def record_outbound(self, message):
        self.messages[(message.tenant_id, message.message_id)] = message
        draft = self.drafts[(message.tenant_id, message.draft_sha256)]
        self.drafts[(message.tenant_id, message.draft_sha256)] = Draft(**{**asdict(draft), "status": "sent"})

    async def booking(self, tenant_id, conversation_id, slot_id):
        return self.bookings.get((tenant_id, conversation_id, slot_id))

    async def save_booking(self, tenant_id, conversation_id, slot_id, document):
        self.bookings[(tenant_id, conversation_id, slot_id)] = document

    async def suppress(self, conversation, actor, reason, created_at_unix):
        self.suppressions.add((conversation.tenant_id, conversation.recipient))
        await self.set_status(conversation.tenant_id, conversation.conversation_id, "suppressed")
