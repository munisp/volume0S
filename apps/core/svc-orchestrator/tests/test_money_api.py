from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from auth import Claims
from main import create_app
from test_api import FakeAuthenticator, FakeQAAssets, FakeWorkflows, auth


class FakeMoney:
    def __init__(self):
        self.calls = []

    async def balance(self, tenant_id, currency):
        self.calls.append(("balance", tenant_id, currency))
        return {"available_minor": "125000", "account_id": "1"}

    async def topup(self, tenant_id, provider, reference, payload):
        self.calls.append(("topup", tenant_id, provider, reference, payload))
        return {"provider": provider, "reference": reference, "status": "awaiting_confirmation",
                "checkout_url": "https://checkout.example/session"}

    async def documents(self, tenant_id):
        self.calls.append(("documents", tenant_id))
        return {"documents": [{"tenant_id": tenant_id, "kind": "invoice", "document_id": "invoice-1",
                               "document_number": "INV-2026-000001", "pdf_sha256": "0" * 64}]}

    async def document_pdf(self, tenant_id, kind, document_id):
        self.calls.append(("pdf", tenant_id, kind, document_id))
        return b"%PDF-tenant-document", "INV-2026-000001"

    async def collection_receipt(self, tenant_id, provider, reference):
        self.calls.append(("receipt", tenant_id, provider, reference))
        return {"event_id": "collection:abc123", "provider": provider, "reference": reference,
                "tenant_id": tenant_id, "disposition": "credited"}

    async def open_suspense(self):
        self.calls.append(("open_suspense",))
        return {"receipts": [{"event_id": "collection:abc123", "provider": "paystack", "reference": "topup-unmatched",
                              "amount_minor": "125000", "currency": "NGN", "disposition": "suspense",
                              "ledger_transfer_id": "123"}]}

    async def resolve_suspense(self, provider, reference, tenant_id, approved_by, reason):
        self.calls.append(("resolve_suspense", provider, reference, tenant_id, approved_by, reason))
        return {"schema": "volumeos.collection-resolution.v1", "resolution_id": "resolution:collection:abc123",
                "event_id": "collection:abc123", "provider": provider, "reference": reference,
                "tenant_id": tenant_id, "amount_minor": "125000", "currency": "NGN",
                "ledger_transfer_id": "456", "approved_by": approved_by, "reason": reason, "status": "resolved"}

    async def ar_aging(self, tenant_id, as_of):
        return {"schema": "volumeos.ar-aging.v1", "tenant_id": tenant_id, "as_of": as_of, "totals": {}, "invoices": []}

    async def export_ar_aging(self, tenant_id, as_of):
        report = await self.ar_aging(tenant_id, as_of)
        return {"report": report, "export": {"sha256": "a" * 64, "object_lock": "COMPLIANCE", "immutable": True}}

    async def partner_statement(self, tenant_id, partner_id, start, end):
        return {"schema": "volumeos.partner-share.v1", "tenant_id": tenant_id, "partner_id": partner_id,
                "period_start": start, "period_end": end, "receipt_count": 0, "totals": {}, "receipts": []}

    async def create_partner_contract(self, tenant_id, payload):
        self.calls.append(("contract", tenant_id, payload))
        return payload | {"tenant_id": tenant_id}

    async def partner_contracts(self, tenant_id, partner_id):
        return {"contracts": []}

    async def trial_balance(self, currency):
        return {"schema": "volumeos.trial-balance.v1", "currency": currency, "balanced": True,
                "total_debits_posted": "0", "total_credits_posted": "0", "accounts": [], "revenue_per_offer": []}

    async def export_trial_balance(self, currency):
        return {"report": await self.trial_balance(currency), "export": {"sha256": "b" * 64}}

    async def financial_export(self, tenant_id, kind, digest):
        self.calls.append(("export", tenant_id, kind, digest))
        return b'{"schema":"volumeos.ar-aging.v1"}', digest


@pytest.fixture
def money_services():
    workflows = FakeWorkflows()
    money = FakeMoney()
    app = create_app(workflows, FakeAuthenticator(), FakeQAAssets(), money)
    with TestClient(app) as client:
        yield client, workflows, money


def cycle_input():
    party = {"legal_name": "Example Operator", "tin": "1234567890", "address": "1 Marina Road, Lagos"}
    return {
        "subscription_id": "sub-1", "offer": "02", "cycle": "2026-10", "issue_date": "2026-10-01",
        "retainer_minor": 5500000, "setup_minor": 250000, "usage_minor": 0, "tax_minor": 0,
        "currency": "NGN", "seller": party, "buyer": party,
        "billing_whatsapp": "+2348012345678",
    }


def billing_snapshot(state="awaiting_funds"):
    return {"tenant_id": "tenant-a", "subscription_id": "sub-1", "cycle": "2026-10",
            "state": state, "dunning_attempts": 1, "recovery_reference": ""}


def test_balance_and_topup_are_derived_from_authenticated_tenant(money_services):
    client, _, money = money_services
    balance = client.get("/v1/billing/balance/NGN", headers=auth())
    assert balance.status_code == 200
    assert balance.json()["available_minor"] == "125000"
    topup = client.post("/v1/billing/topups/paystack/topup-1", headers=auth(), json={
        "amount_minor": 125000, "currency": "NGN", "customer_email": "owner@example.com",
        "redirect_url": "https://console.example/billing/callback",
    })
    assert topup.status_code == 201
    assert money.calls[0] == ("balance", "tenant-a", "NGN")
    assert money.calls[1][1:4] == ("tenant-a", "paystack", "topup-1")
    assert money.calls[1][4]["amount_minor"] == "125000"


def test_billing_cycle_start_uses_fixed_dunning_policy_and_workflow_identity(money_services):
    client, workflows, _ = money_services
    response = client.post("/v1/billing/cycles", headers=auth(), json=cycle_input())
    # The default test principal is delivery, not sales.
    assert response.status_code == 403
    app = create_app(workflows, FakeAuthenticator(Claims("tenant-a", "seller-1", frozenset({"sales"}))),
                     FakeQAAssets(), FakeMoney())
    with TestClient(app) as sales_client:
        response = sales_client.post("/v1/billing/cycles", headers=auth(), json=cycle_input())
    assert response.status_code == 202
    kind, argument, workflow_id, queue = workflows.started[0]
    assert kind == "BillingCycleWorkflow"
    assert argument["tenant_id"] == "tenant-a"
    assert argument["dunning_delays_seconds"] == [259200, 604800, 1209600]
    assert workflow_id == "billing:tenant-a:sub-1:2026-10"
    assert queue == "billing-v1"


def test_cycle_feed_is_tenant_prefixed(money_services):
    client, workflows, _ = money_services
    workflows.snapshots["billing:tenant-a:sub-1:2026-10"] = billing_snapshot("paused")
    workflows.snapshots["billing:tenant-b:sub-2:2026-10"] = billing_snapshot("paused") | {"tenant_id": "tenant-b"}
    response = client.get("/v1/billing/cycles", headers=auth())
    assert response.status_code == 200
    assert workflows.prefixes == ["billing:tenant-a:"]
    assert len(response.json()["cycles"]) == 1


def test_recovery_requires_current_collection_receipt_and_matching_cycle(money_services):
    client, workflows, money = money_services
    workflow_id = "billing:tenant-a:sub-1:2026-10"
    workflows.snapshots[workflow_id] = billing_snapshot("paused")
    response = client.post("/v1/billing/recovery", headers=auth(), json={
        "subscription_id": "sub-1", "cycle": "2026-10", "provider": "paystack", "reference": "topup-1",
    })
    assert response.status_code == 202
    assert money.calls == [("receipt", "tenant-a", "paystack", "topup-1")]
    assert workflows.signals == [(workflow_id, "funding_recovered", {"recovery_reference": "collection:abc123"})]

    workflows.snapshots[workflow_id] = billing_snapshot("settled")
    conflict = client.post("/v1/billing/recovery", headers=auth(), json={
        "subscription_id": "sub-1", "cycle": "2026-10", "provider": "paystack", "reference": "topup-1",
    })
    assert conflict.status_code == 409
    assert len(workflows.signals) == len(money.calls) == 1


def finance_client(tenant="tenant-a", money=None):
    app = create_app(
        FakeWorkflows(), FakeAuthenticator(Claims(tenant, "finance-1", frozenset({"finance"}))),
        FakeQAAssets(), money or FakeMoney(),
    )
    return TestClient(app)


def test_financial_reports_and_exports_enforce_finance_and_platform_scope(money_services):
    client, _, _ = money_services
    aging = client.get("/v1/billing/reports/ar-aging?as_of=2026-10-28", headers=auth())
    assert aging.status_code == 200 and aging.json()["tenant_id"] == "tenant-a"
    denied = client.post("/v1/billing/reports/ar-aging/exports", headers=auth(), json={"as_of": "2026-10-28"})
    assert denied.status_code == 403
    with finance_client() as finance:
        exported = finance.post("/v1/billing/reports/ar-aging/exports", headers=auth(), json={"as_of": "2026-10-28"})
        assert exported.status_code == 200
        tenant_trial = finance.get("/v1/billing/reports/trial-balance/NGN", headers=auth())
        assert tenant_trial.status_code == 403
        contract = finance.post("/v1/billing/partner-contracts", headers=auth(), json={
            "contract_id": "contract-1", "partner_id": "partner-1", "offer": "02", "basis_points": 2000,
            "effective_from": "2026-01-01T00:00:00Z",
        })
        assert contract.status_code == 201 and contract.json()["tenant_id"] == "tenant-a"
    with finance_client("platform") as platform:
        trial = platform.get("/v1/billing/reports/trial-balance/NGN", headers=auth())
        assert trial.status_code == 200 and trial.json()["balanced"] is True


def test_partner_statement_and_locked_export_download(money_services):
    client, _, money = money_services
    statement = client.get(
        "/v1/billing/reports/partner-share/partner-1?start=2026-01-01&end=2026-12-31", headers=auth(),
    )
    assert statement.status_code == 200 and statement.json()["partner_id"] == "partner-1"
    digest = "c" * 64
    download = client.get(f"/v1/billing/financial-exports/partner-share/{digest}", headers=auth())
    assert download.status_code == 200
    assert money.calls[-1] == ("export", "tenant-a", "partner-share", digest)
    invalid_period = client.get(
        "/v1/billing/reports/partner-share/partner-1?start=2026-12-31&end=2026-01-01", headers=auth(),
    )
    assert invalid_period.status_code == 400


def test_suspense_review_requires_platform_finance_and_binds_actor(money_services):
    client, _, money = money_services
    assert client.get("/v1/billing/collection-suspense", headers=auth()).status_code == 403
    with finance_client() as tenant_finance:
        assert tenant_finance.get("/v1/billing/collection-suspense", headers=auth()).status_code == 403
    with finance_client("platform", money) as platform:
        queue = platform.get("/v1/billing/collection-suspense", headers=auth())
        assert queue.status_code == 200 and len(queue.json()["receipts"]) == 1
        resolved = platform.post(
            "/v1/billing/collection-suspense/paystack/topup-unmatched/resolutions",
            headers=auth(),
            json={"tenant_id": "tenant-a", "reason": "Matched signed client invoice"},
        )
        assert resolved.status_code == 200
        assert resolved.json()["approved_by"] == "finance-1"
    assert money.calls[-1] == (
        "resolve_suspense", "paystack", "topup-unmatched", "tenant-a", "finance-1",
        "Matched signed client invoice",
    )
