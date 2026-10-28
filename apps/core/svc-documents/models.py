"""Immutable billing document and provider-delivery contracts."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

DocumentKind = Literal["invoice", "receipt"]
SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
E164 = re.compile(r"^\+[1-9]\d{7,14}$")


@dataclass(frozen=True)
class StoredDocument:
    kind: DocumentKind
    document_id: str
    tenant_id: str
    document_number: str
    structured: dict[str, Any]
    pdf: bytes
    created_at: datetime

    def metadata(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "document_id": self.document_id,
            "tenant_id": self.tenant_id,
            "document_number": self.document_number,
            "structured": self.structured,
            "created_at": self.created_at.isoformat(),
            "pdf_sha256": __import__("hashlib").sha256(self.pdf).hexdigest(),
        }


@dataclass(frozen=True)
class Delivery:
    delivery_id: str
    tenant_id: str
    document_kind: DocumentKind
    document_id: str
    channel: Literal["whatsapp"]
    recipient: str
    provider_message_id: str
    status: Literal["provider_accepted"]
    created_at: datetime

    def json(self) -> dict[str, Any]:
        return {
            "delivery_id": self.delivery_id,
            "tenant_id": self.tenant_id,
            "document_kind": self.document_kind,
            "document_id": self.document_id,
            "channel": self.channel,
            "recipient": self.recipient,
            "provider_message_id": self.provider_message_id,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


def validate_scope(tenant_id: str, document_id: str, kind: str) -> None:
    if not SAFE_ID.fullmatch(tenant_id) or not SAFE_ID.fullmatch(document_id):
        raise ValueError("valid tenant and document identifiers are required")
    if kind not in {"invoice", "receipt"}:
        raise ValueError("document kind must be invoice or receipt")


def validate_recipient(value: str) -> None:
    if not E164.fullmatch(value):
        raise ValueError("recipient must be an E.164 WhatsApp number")
