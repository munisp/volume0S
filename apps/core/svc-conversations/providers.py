"""HTTPS provider adapters for redacted AI, WhatsApp Cloud, and booking."""
from __future__ import annotations

import os
from urllib.parse import quote

import httpx

from conversations import Conversation, ConversationError, Draft


def _https(value: str, label: str) -> str:
    if not value.startswith("https://"):
        raise RuntimeError(f"{label} must be an HTTPS URL")
    return value.rstrip("/")


class PrivacyAIClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str, token: str):
        if not token:
            raise RuntimeError("VOLUMEOS_SERVICE_TOKEN is required")
        self.client, self.base_url, self.token = client, _https(base_url, "PRIVACY_SERVICE_URL"), token

    async def complete(self, tenant_id: str, prompt: str, model: str) -> dict:
        response = await self.client.post(
            self.base_url + "/v1/ai/redacted-completion",
            json={"prompt": prompt, "model": model, "max_tokens": 1024},
            headers={"Authorization": f"Bearer {self.token}", "X-VolumeOS-Tenant": tenant_id},
        )
        response.raise_for_status()
        document = response.json()
        if not isinstance(document, dict):
            raise ConversationError("redacted AI gateway returned a non-object response")
        return document


class WhatsAppCloudSender:
    def __init__(self, client: httpx.AsyncClient, base_url: str, access_token: str):
        if not access_token:
            raise RuntimeError("WHATSAPP_ACCESS_TOKEN is required")
        self.client, self.base_url, self.token = client, _https(base_url, "WHATSAPP_API_URL"), access_token

    async def send(self, conversation: Conversation, draft: Draft) -> dict:
        response = await self.client.post(
            f"{self.base_url}/{conversation.phone_number_id}/messages",
            json={"messaging_product": "whatsapp", "to": conversation.recipient,
                  "type": "text", "text": {"body": draft.response_text}},
            headers={"Authorization": f"Bearer {self.token}",
                     "Idempotency-Key": f"frontdesk:{draft.tenant_id}:{draft.digest}"},
        )
        response.raise_for_status()
        document = response.json()
        if not isinstance(document, dict):
            raise ConversationError("WhatsApp returned a non-object response")
        return document


class HTTPBookingAdapter:
    def __init__(self, client: httpx.AsyncClient, base_url: str, token: str):
        if not token:
            raise RuntimeError("VOLUMEOS_SERVICE_TOKEN is required")
        self.client, self.base_url, self.token = client, _https(base_url, "BOOKING_SERVICE_URL"), token

    async def _post(self, path: str, body: dict, tenant_id: str, key: str) -> dict:
        response = await self.client.post(
            self.base_url + path, json=body,
            headers={"Authorization": f"Bearer {self.token}", "X-VolumeOS-Tenant": tenant_id,
                     "Idempotency-Key": key},
        )
        response.raise_for_status()
        document = response.json()
        if not isinstance(document, dict):
            raise ConversationError("booking adapter returned a non-object response")
        return document

    async def hold(self, tenant_id: str, conversation_id: str, slot_id: str, idempotency_key: str) -> dict:
        return await self._post("/v1/booking-holds", {"conversation_id": conversation_id, "slot_id": slot_id},
                                tenant_id, idempotency_key)

    async def confirm(self, tenant_id: str, hold_id: str, idempotency_key: str) -> dict:
        return await self._post(f"/v1/booking-holds/{quote(hold_id, safe='')}/confirm", {}, tenant_id, idempotency_key)
