"""Offer 04: signed WhatsApp intake, redacted agent drafts, escalation, and booking."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import math
import re
from typing import Any, Protocol

from common import Artifact, Approval, OfferError, RedactedAIClient, canonical, identifier, release_artifact, sha256

ESCALATION_TERMS = {"refund", "lawyer", "complaint", "angry", "fraud", "emergency", "human", "manager"}


def verify_whatsapp_signature(raw_body: bytes, signature: str, app_secret: str) -> None:
    if not app_secret or not signature.startswith("sha256="):
        raise OfferError("WhatsApp signature is required")
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature[7:], expected):
        raise OfferError("WhatsApp signature mismatch")


def inbound_message(raw_body: bytes, signature: str, app_secret: str) -> dict[str, Any]:
    verify_whatsapp_signature(raw_body, signature, app_secret)
    import json
    document = json.loads(raw_body)
    try:
        value = document["entry"][0]["changes"][0]["value"]
        message = value["messages"][0]
        contact = value["contacts"][0]
        result = {"message_id": message["id"], "from": message["from"], "type": message["type"],
                  "text": message.get("text", {}).get("body", ""), "timestamp": int(message["timestamp"]),
                  "contact_name": contact.get("profile", {}).get("name", "")}
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise OfferError("unsupported WhatsApp webhook payload") from exc
    if not result["message_id"] or not re.fullmatch(r"234[789][01]\d{8}", result["from"]) or len(result["text"]) > 4096:
        raise OfferError("invalid WhatsApp message identity, phone, or body")
    return result


@dataclass(frozen=True)
class AgentDraft:
    tenant_id: str
    conversation_id: str
    inbound_message_id: str
    response_text: str
    confidence: float
    model_version: str
    escalation_reason: str | None = None

    def validated(self) -> "AgentDraft":
        identifier(self.tenant_id, "tenant"); identifier(self.conversation_id, "conversation"); identifier(self.model_version, "model")
        if not self.inbound_message_id or not self.response_text.strip() or len(self.response_text) > 4096:
            raise OfferError("agent draft must bind an inbound message and bounded response")
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise OfferError("agent confidence must be normalized")
        return self

    @property
    def digest(self) -> str:
        self.validated()
        return sha256(canonical({"tenant_id": self.tenant_id, "conversation_id": self.conversation_id,
                                 "inbound_message_id": self.inbound_message_id, "response_text": self.response_text,
                                 "confidence": self.confidence, "model_version": self.model_version,
                                 "escalation_reason": self.escalation_reason}))


def escalation_reason(text: str, confidence: float, threshold: float = 0.7) -> str | None:
    terms = {term for term in ESCALATION_TERMS if re.search(rf"\b{re.escape(term)}\b", text, re.I)}
    if terms:
        return "keyword:" + ",".join(sorted(terms))
    if confidence < threshold:
        return "low-confidence"
    return None


async def draft_reply(tenant_id: str, conversation_id: str, message: dict[str, Any], opted_in: bool,
                      ai: RedactedAIClient, model_version: str, confidence: float | None = None) -> AgentDraft:
    if not opted_in:
        raise OfferError("WhatsApp opt-in/suppression check failed")
    result = await ai.complete(tenant_id, message["text"], model_version)
    response = str(result.get("output_text", "")).strip()
    if confidence is None:
        confidence = result.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise OfferError("redacted AI response must include numeric confidence")
    confidence = float(confidence)
    reason = escalation_reason(message["text"] + "\n" + response, confidence)
    return AgentDraft(tenant_id, conversation_id, message["message_id"], response, confidence, model_version, reason).validated()


def draft_artifact(draft: AgentDraft) -> Artifact:
    payload = canonical({"response_text": draft.response_text, "inbound_message_id": draft.inbound_message_id,
                         "confidence": draft.confidence, "model_version": draft.model_version,
                         "escalation_reason": draft.escalation_reason}).encode()
    return Artifact(draft.tenant_id, "04", draft.conversation_id, 1,
                    f"{draft.tenant_id}/offers/04/{draft.conversation_id}/reply-{draft.inbound_message_id}.json",
                    sha256(payload), "application/json").validated()


def approved_reply(draft: AgentDraft, approvals: list[Approval]) -> AgentDraft:
    artifact = draft_artifact(draft)
    # Artifact digest covers response metadata; approval also binds the complete AgentDraft digest.
    if not any(a.decision == "approved" and a.artifact_sha256 in {artifact.sha256, draft.digest} for a in approvals):
        raise OfferError("human approval is required for the exact front-desk reply")
    return draft


class BookingAdapter(Protocol):
    async def hold(self, tenant_id: str, conversation_id: str, slot_id: str, idempotency_key: str) -> dict[str, Any]: ...
    async def confirm(self, tenant_id: str, hold_id: str, idempotency_key: str) -> dict[str, Any]: ...


async def book(adapter: BookingAdapter, tenant_id: str, conversation_id: str, slot_id: str) -> dict[str, Any]:
    identifier(slot_id, "booking slot")
    hold = await adapter.hold(tenant_id, conversation_id, slot_id, f"booking-hold:{tenant_id}:{conversation_id}:{slot_id}")
    hold_id = str(hold.get("hold_id", ""))
    identifier(hold_id, "booking hold")
    confirmed = await adapter.confirm(tenant_id, hold_id, f"booking-confirm:{tenant_id}:{conversation_id}:{hold_id}")
    if confirmed.get("status") != "confirmed" or confirmed.get("hold_id") != hold_id:
        raise OfferError("booking adapter did not confirm the exact hold")
    return confirmed


class WhatsAppCloudClient:
    def __init__(self, transport, endpoint: str, access_token: str, phone_number_id: str):
        if not endpoint.startswith("https://") or not access_token or not phone_number_id:
            raise OfferError("HTTPS WhatsApp endpoint and credentials required")
        self.transport, self.endpoint, self.token, self.phone_number_id = transport, endpoint.rstrip("/"), access_token, phone_number_id

    async def send(self, draft: AgentDraft, approvals: list[Approval], recipient: str) -> dict[str, Any]:
        approved_reply(draft, approvals)
        if not re.fullmatch(r"234[789][01]\d{8}", recipient):
            raise OfferError("invalid WhatsApp recipient")
        response = await self.transport.post(f"{self.endpoint}/{self.phone_number_id}/messages",
            json={"messaging_product": "whatsapp", "to": recipient, "type": "text", "text": {"body": draft.response_text}},
            headers={"Authorization": f"Bearer {self.token}", "Idempotency-Key": f"frontdesk:{draft.tenant_id}:{draft.digest}"})
        response.raise_for_status(); document = response.json()
        if not document.get("messages", [{}])[0].get("id"):
            raise OfferError("WhatsApp did not accept the approved message")
        return document
