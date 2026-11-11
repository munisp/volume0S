"""Outbound channels and the services a send must ask first.

- Email goes through Resend (from a verified upi.dev address, named after the business), with a one-click
  unsubscribe link that suppresses the address in svc-tenancy.
- WhatsApp: Meta only lets a business start a conversation with a pre-approved template, so campaign messages go
  out through one configured template whose single body variable carries the drafted text.
- svc-tenancy decides every send (kill switch, lifecycle state, suppression list) before anything is sent.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import os
import re
from dataclasses import dataclass

import httpx

TEST_DOMAINS = [d.strip().lower() for d in os.environ.get("EMAIL_TEST_DOMAINS", ".test,example.com").split(",") if d.strip()]
PROVIDER_ID = re.compile(r"^[a-zA-Z0-9._:=-]{1,255}$")


class ChannelError(RuntimeError):
    pass


def is_test_address(email: str) -> bool:
    domain = email.rsplit("@", 1)[-1].lower()
    return any(domain == d.lstrip(".") or (d.startswith(".") and domain.endswith(d)) for d in TEST_DOMAINS)


def is_test_phone(phone: str) -> bool:
    """+234 700 000 xxxx (unallocated) and +1 555 01xx numbers are test numbers: never sent to."""
    return phone.startswith("+234700000") or phone.startswith("+155501")


# Unsubscribe links ----------------------------------------------------------------------------------------------
def _key() -> bytes:
    secret = os.environ.get("VOLUMEOS_SERVICE_TOKEN", "")
    return hmac.new(secret.encode(), b"volumeos.campaigns.unsubscribe", hashlib.sha256).digest()


def unsubscribe_token(tenant_id: str, email: str) -> str:
    body = f"{tenant_id}\x1f{email.lower()}".encode()
    mac = hmac.new(_key(), body, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(body + mac).decode().rstrip("=")


def read_unsubscribe_token(token: str) -> tuple[str, str] | None:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (ValueError, TypeError):
        return None
    if len(raw) < 18:
        return None
    body, mac = raw[:-16], raw[-16:]
    if not hmac.compare_digest(mac, hmac.new(_key(), body, hashlib.sha256).digest()[:16]):
        return None
    try:
        tenant_id, email = body.decode().split("\x1f", 1)
    except ValueError:
        return None
    return tenant_id, email


# Email ----------------------------------------------------------------------------------------------------------
@dataclass
class EmailMessage:
    to: str
    business: str
    subject: str
    body: str
    unsubscribe_url: str


class ResendEmail:
    def __init__(self, api_key: str, from_address: str):
        self.api_key = api_key
        self.from_address = from_address

    @classmethod
    def from_env(cls) -> "ResendEmail | None":
        key = os.environ.get("RESEND_API_KEY", "").strip()
        return cls(key, os.environ.get("CAMPAIGN_FROM_ADDRESS", "campaigns@upi.dev")) if key else None

    async def send(self, message: EmailMessage) -> str:
        sender_name = re.sub(r"[^\w .&'-]", "", message.business).strip()[:60] or "VolumeOS"
        text = f"{message.body}\n\n—\n{message.business}\nTo stop these emails: {message.unsubscribe_url}\n"
        paragraphs = "".join(f"<p>{html.escape(p)}</p>" for p in message.body.split("\n") if p.strip())
        body = (f'<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;font-size:15px;line-height:1.55;'
                f'color:#1a1a1a;max-width:560px">{paragraphs}<p style="color:#666;font-size:13px;margin-top:28px">'
                f'{html.escape(message.business)} · <a href="{html.escape(message.unsubscribe_url)}" style="color:#666">'
                f'Unsubscribe</a></p></div>')
        payload = {
            "from": f"{sender_name} <{self.from_address}>", "to": [message.to], "subject": message.subject,
            "text": text, "html": body,
            "headers": {"List-Unsubscribe": f"<{message.unsubscribe_url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post("https://api.resend.com/emails", json=payload,
                                             headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError as exc:
            raise ChannelError(f"Resend unreachable: {exc}") from exc
        if response.status_code // 100 != 2:
            raise ChannelError(f"Resend returned {response.status_code}: {response.text[:200]}")
        message_id = str(response.json().get("id", ""))
        if not PROVIDER_ID.fullmatch(message_id):
            raise ChannelError("Resend answer has no message id")
        return message_id


# WhatsApp -------------------------------------------------------------------------------------------------------
class WhatsAppTemplate:
    def __init__(self, api_url: str, token: str, phone_number_id: str, template: str, language: str):
        self.base = api_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.phone_number_id = phone_number_id
        self.template = template
        self.language = language

    @classmethod
    def from_env(cls) -> "WhatsAppTemplate | None":
        token = os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip()
        number = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
        template = os.environ.get("WHATSAPP_CAMPAIGN_TEMPLATE", "").strip()
        if not (token and number and template):
            return None
        version = os.environ.get("WHATSAPP_API_VERSION", "v23.0")
        return cls(os.environ.get("WHATSAPP_API_URL", f"https://graph.facebook.com/{version}"), token, number, template,
                   os.environ.get("WHATSAPP_CAMPAIGN_TEMPLATE_LANGUAGE", "en"))

    async def send(self, to: str, text: str) -> str:
        payload = {
            "messaging_product": "whatsapp", "to": to.lstrip("+"), "type": "template",
            "template": {"name": self.template, "language": {"code": self.language},
                         "components": [{"type": "body", "parameters": [{"type": "text", "text": text[:1000]}]}]},
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(f"{self.base}/{self.phone_number_id}/messages", json=payload, headers=self.headers)
        except httpx.HTTPError as exc:
            raise ChannelError(f"WhatsApp unreachable: {exc}") from exc
        if response.status_code // 100 != 2:
            raise ChannelError(f"WhatsApp returned {response.status_code}: {response.text[:200]}")
        messages = response.json().get("messages") or []
        message_id = str(messages[0].get("id", "")) if messages else ""
        if not PROVIDER_ID.fullmatch(message_id):
            raise ChannelError("WhatsApp answer has no message id")
        return message_id


# Other VolumeOS services ----------------------------------------------------------------------------------------
class Services:
    """svc-tenancy (business name, may-I-send, suppressions) and svc-provisioning (who to remind about billing)."""

    def __init__(self, tenancy_url: str, provisioning_url: str, token: str):
        self.tenancy_url = tenancy_url.rstrip("/")
        self.provisioning_url = provisioning_url.rstrip("/")
        self.token = token

    @classmethod
    def from_env(cls) -> "Services":
        return cls(os.environ.get("TENANCY_URL", ""), os.environ.get("PROVISIONING_URL", ""),
                   os.environ.get("VOLUMEOS_SERVICE_TOKEN", ""))

    def _headers(self, tenant_id: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "X-VolumeOS-Tenant": tenant_id}

    async def business(self, tenant_id: str) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(self.tenancy_url + "/v1/tenants/current", headers=self._headers(tenant_id))
        response.raise_for_status()
        return response.json()

    async def business_name(self, tenant_id: str) -> str:
        return str((await self.business(tenant_id)).get("name", ""))

    async def may_send(self, tenant_id: str, channel: str, recipient: str) -> tuple[bool, str]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(self.tenancy_url + "/v1/dispatch-authorizations", headers=self._headers(tenant_id),
                                         json={"channel": channel, "recipient": recipient})
        response.raise_for_status()
        decision = response.json()
        return bool(decision.get("allowed")), str(decision.get("reason", ""))

    async def suppress(self, tenant_id: str, channel: str, recipient: str, reason: str) -> None:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.put(self.tenancy_url + "/v1/suppressions", headers=self._headers(tenant_id),
                                        json={"channel": channel, "recipient": recipient, "reason": reason})
        response.raise_for_status()

    async def billing_contacts(self, tenant_id: str) -> list[dict[str, str]]:
        """The business's owners (client-owner role, sign-in on): they fund the balance, so they get reminders."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(self.provisioning_url + "/v1/people", headers=self._headers(tenant_id))
        response.raise_for_status()
        return [p for p in response.json().get("people", []) if p.get("enabled") and "client-owner" in (p.get("roles") or [])]
