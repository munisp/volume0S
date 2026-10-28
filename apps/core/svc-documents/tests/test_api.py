import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from main import create_app
from models import Delivery, StoredDocument
from whatsapp import WhatsAppDocuments, WhatsAppError

TOKEN = "test-service-token-with-at-least-32-bytes"
NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


def document(kind="invoice"):
    return StoredDocument(
        kind, f"{kind}-1", "tenant-a", f"{kind.upper()}-2026-000001",
        {"document_number": f"{kind.upper()}-2026-000001", "total_minor": "12500"},
        b"%PDF-immutable", NOW,
    )


class FakeStore:
    def __init__(self):
        self.documents = []
        self.deliveries = {}

    async def ready(self):
        return None

    async def list_documents(self, tenant_id, limit):
        assert tenant_id == "tenant-a"
        return [document().metadata()]

    async def get_document(self, tenant_id, kind, document_id):
        if tenant_id != "tenant-a" or document_id != f"{kind}-1":
            return None
        return document(kind)

    async def get_delivery(self, tenant_id, kind, document_id, recipient):
        return self.deliveries.get((tenant_id, kind, document_id, recipient))

    async def record_delivery(self, tenant_id, kind, document_id, recipient, provider_message_id):
        delivery = Delivery(
            "delivery-1", tenant_id, kind, document_id, "whatsapp", recipient,
            provider_message_id, "provider_accepted", NOW,
        )
        self.deliveries[(tenant_id, kind, document_id, recipient)] = delivery
        return delivery


class FakeWhatsApp:
    def __init__(self):
        self.calls = []

    async def deliver(self, doc, recipient):
        self.calls.append((doc.document_id, recipient))
        return "wamid.accepted-1"


def client():
    store = FakeStore()
    whatsapp = FakeWhatsApp()
    return TestClient(create_app(store, whatsapp, TOKEN)), store, whatsapp


def headers(tenant="tenant-a"):
    return {"Authorization": f"Bearer {TOKEN}", "X-VolumeOS-Tenant": tenant}


def test_document_list_and_pdf_are_tenant_scoped_and_never_cached():
    api, _, _ = client()
    listed = api.get("/v1/tenants/tenant-a/documents", headers=headers())
    assert listed.status_code == 200
    item = listed.json()["documents"][0]
    assert item["document_id"] == "invoice-1"
    assert "pdf" not in item and len(item["pdf_sha256"]) == 64
    pdf = api.get("/v1/tenants/tenant-a/documents/invoice/invoice-1/pdf", headers=headers())
    assert pdf.status_code == 200
    assert pdf.content == b"%PDF-immutable"
    assert pdf.headers["Cache-Control"] == "no-store"
    assert "attachment" in pdf.headers["Content-Disposition"]
    denied = api.get("/v1/tenants/tenant-a/documents/invoice/invoice-1/pdf", headers=headers("tenant-b"))
    assert denied.status_code == 403


def test_delivery_replay_uses_recorded_provider_acceptance():
    api, _, whatsapp = client()
    path = "/v1/tenants/tenant-a/documents/receipt/receipt-1/deliveries"
    body = {"channel": "whatsapp", "recipient": "+2348012345678"}
    first = api.post(path, headers=headers(), json=body)
    second = api.post(path, headers=headers(), json=body)
    assert first.status_code == 202
    assert second.status_code == 200
    assert first.json()["status"] == "provider_accepted"
    assert first.json()["provider_message_id"] == "wamid.accepted-1"
    assert whatsapp.calls == [("receipt-1", "+2348012345678")]


def test_invalid_recipient_and_missing_document_do_not_call_provider():
    api, _, whatsapp = client()
    path = "/v1/tenants/tenant-a/documents/invoice/invoice-1/deliveries"
    invalid = api.post(path, headers=headers(), json={"channel": "whatsapp", "recipient": "08012345678"})
    missing = api.post(
        "/v1/tenants/tenant-a/documents/invoice/unknown/deliveries",
        headers=headers(), json={"channel": "whatsapp", "recipient": "+2348012345678"},
    )
    assert invalid.status_code == 400
    assert missing.status_code == 404
    assert whatsapp.calls == []


async def test_whatsapp_uploads_immutable_pdf_then_sends_document():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/media"):
            assert b"%PDF-immutable" in request.content
            return httpx.Response(200, json={"id": "media-1"})
        assert request.url.path.endswith("/messages")
        assert json.loads(request.content)["document"]["id"] == "media-1"
        assert json.loads(request.content)["to"] == "+2348012345678"
        return httpx.Response(200, json={"messages": [{"id": "wamid-1"}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = WhatsAppDocuments("https://graph.facebook.com/v23.0", "secret", "phone-1", http)
    assert await provider.deliver(document(), "+2348012345678") == "wamid-1"
    assert len(seen) == 2
    assert all(request.headers["Authorization"] == "Bearer secret" for request in seen)
    await http.aclose()


async def test_whatsapp_rejects_insecure_endpoint_and_invalid_provider_receipt():
    with pytest.raises(ValueError):
        WhatsAppDocuments("http://graph.facebook.com", "secret", "phone-1")
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})))
    provider = WhatsAppDocuments("http://127.0.0.1:8080", "secret", "phone-1", http)
    with pytest.raises(WhatsAppError):
        await provider.deliver(document(), "+2348012345678")
    await http.aclose()
