"""WhatsApp Cloud API document delivery with explicit provider acceptance."""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

import httpx

from models import StoredDocument

PROVIDER_ID = re.compile(r"^[a-zA-Z0-9._:-]{1,255}$")


class WhatsAppError(RuntimeError):
    pass


class WhatsAppDocuments:
    def __init__(self, api_url: str, access_token: str, phone_number_id: str, client: httpx.AsyncClient | None = None):
        parsed = urlparse(api_url)
        secure = parsed.scheme == "https"
        try:
            loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
        except ValueError:
            loopback = False
        if not secure and not (parsed.scheme == "http" and loopback):
            raise ValueError("WhatsApp API must use HTTPS")
        if not access_token or not phone_number_id.strip():
            raise ValueError("WhatsApp access token and phone number ID are required")
        self.base_url = api_url.rstrip("/")
        self.phone_number_id = phone_number_id
        self.client = client
        self.headers = {"Authorization": f"Bearer {access_token}"}

    @classmethod
    def from_env(cls) -> "WhatsAppDocuments":
        import os
        version = os.environ.get("WHATSAPP_API_VERSION", "v23.0")
        return cls(
            os.environ.get("WHATSAPP_API_URL", f"https://graph.facebook.com/{version}"),
            os.environ.get("WHATSAPP_ACCESS_TOKEN", ""),
            os.environ.get("WHATSAPP_PHONE_NUMBER_ID", ""),
        )

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        kwargs.setdefault("timeout", 30.0)
        if self.client is None:
            async with httpx.AsyncClient() as client:
                response = await client.request(method, self.base_url + path, headers=self.headers, **kwargs)
        else:
            response = await self.client.request(method, self.base_url + path, headers=self.headers, **kwargs)
        response.raise_for_status()
        return response

    async def deliver(self, document: StoredDocument, recipient: str) -> str:
        filename = f"{document.document_number}.pdf"
        uploaded = await self._request(
            "POST", f"/{self.phone_number_id}/media",
            data={"messaging_product": "whatsapp", "type": "document"},
            files={"file": (filename, document.pdf, "application/pdf")},
        )
        media_id = str(uploaded.json().get("id", ""))
        if not PROVIDER_ID.fullmatch(media_id):
            raise WhatsAppError("WhatsApp media response is missing a valid ID")
        sent = await self._request(
            "POST", f"/{self.phone_number_id}/messages",
            json={
                "messaging_product": "whatsapp", "recipient_type": "individual", "to": recipient,
                "type": "document", "document": {
                    "id": media_id, "filename": filename,
                    "caption": f"VolumeOS {document.kind} {document.document_number}",
                },
            },
        )
        body = sent.json()
        messages = body.get("messages") if isinstance(body, dict) else None
        message_id = str(messages[0].get("id", "")) if isinstance(messages, list) and messages else ""
        if not PROVIDER_ID.fullmatch(message_id):
            raise WhatsAppError("WhatsApp send response is missing a valid message ID")
        return message_id
