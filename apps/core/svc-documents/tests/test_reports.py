import hashlib
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from calculations import ar_aging_rows, partner_share_rows
from main import create_app
from reports import BillingReports, FinancialExports, ReportError

TOKEN = "test-service-token-with-at-least-32-bytes"
NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


def trial_balance():
    return {
        "schema": "volumeos.trial-balance.v1", "currency": "NGN", "balanced": True,
        "account_count": 0, "total_debits_posted": "0", "total_credits_posted": "0",
        "accounts": [], "revenue_per_offer": [],
    }


async def test_billing_report_is_validated_and_platform_scoped():
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["X-VolumeOS-Tenant"] == "platform"
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(200, json=trial_balance())

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    reports = BillingReports("http://billing:8080", TOKEN, http)
    assert (await reports.trial_balance("NGN"))["balanced"] is True
    assert seen[0].url.path == "/v1/tenants/platform/reports/trial-balance/NGN"
    await http.aclose()


async def test_unbalanced_or_malformed_trial_balance_fails_closed():
    bad = trial_balance()
    bad["total_credits_posted"] = "1"
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=bad)))
    reports = BillingReports("http://billing:8080", TOKEN, http)
    with pytest.raises(ReportError):
        await reports.trial_balance("NGN")
    await http.aclose()


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.puts = 0

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return self.objects[Key]["metadata"]

    def put_object(self, **kwargs):
        self.puts += 1
        key = kwargs["Key"]
        assert key
        self.objects[key] = {
            "content": kwargs["Body"],
            "metadata": {
                "ObjectLockMode": kwargs["ObjectLockMode"],
                "ObjectLockRetainUntilDate": kwargs["ObjectLockRetainUntilDate"],
            },
        }

    def get_object(self, Bucket, Key):
        class Body:
            def __init__(self, content):
                self.content = content

            def read(self, _limit):
                return self.content

        return {"Body": Body(self.objects[Key]["content"])}


def test_export_is_content_addressed_locked_and_replay_does_not_overwrite():
    s3 = FakeS3()
    exports = FinancialExports("finance-archive", s3, now=lambda: NOW)
    first = exports.put("tenant-a", "ar-aging", {"b": 2, "a": "12500"})
    second = exports.put("tenant-a", "ar-aging", {"a": "12500", "b": 2})
    assert first["created"] is True and second["created"] is False
    assert first["sha256"] == second["sha256"] == hashlib.sha256(b'{"a":"12500","b":2}').hexdigest()
    assert first["object_lock"] == "COMPLIANCE" and s3.puts == 1
    retained = datetime.fromisoformat(first["retained_until"])
    assert retained >= NOW + timedelta(days=2555)
    content, metadata = exports.get("tenant-a", "ar-aging", first["sha256"])
    assert content == b'{"a":"12500","b":2}' and metadata["immutable"] is True


def test_export_rejects_missing_retention_and_corrupt_content():
    s3 = FakeS3()
    exports = FinancialExports("finance-archive", s3, now=lambda: NOW)
    saved = exports.put("tenant-a", "trial-balance", trial_balance())
    s3.objects[saved["object_key"]]["metadata"]["ObjectLockMode"] = None
    with pytest.raises(ReportError):
        exports.get("tenant-a", "trial-balance", saved["sha256"])
    s3.objects[saved["object_key"]]["metadata"]["ObjectLockMode"] = "COMPLIANCE"
    s3.objects[saved["object_key"]]["content"] = b"corrupt"
    with pytest.raises(ReportError):
        exports.get("tenant-a", "trial-balance", saved["sha256"])


def test_ar_and_partner_calculations_use_exact_minor_units():
    rows = [{
        "id": "inv-1", "document_number": "INV-1", "issue_date": date(2026, 8, 1),
        "currency": "NGN", "total_minor": 10001, "paid_minor": 1,
    }]
    aging = ar_aging_rows("tenant-a", date(2026, 10, 28), rows)
    assert aging["invoices"][0]["age_days"] == 88
    assert aging["invoices"][0]["bucket"] == "days_61_90"
    assert aging["totals"]["NGN"]["outstanding_minor"] == "10000"
    posted = [{
        "id": "receipt-1", "document_number": "RCT-1", "offer": "02", "contract_id": "contract-1",
        "ledger_transfer_id": 99, "created_at": NOW, "currency": "NGN",
        "amount_minor": 340282366920938463463374607431768211455, "basis_points": 3333,
    }]
    statement = partner_share_rows("tenant-a", "partner-1", date(2026, 1, 1), date(2026, 12, 31), posted)
    expected = 340282366920938463463374607431768211455 * 3333 // 10000
    assert statement["totals"]["NGN"]["partner_share_minor"] == str(expected)


class FakeReportStore:
    async def ready(self):
        return None

    async def ar_aging(self, tenant_id, as_of):
        return {"schema": "volumeos.ar-aging.v1", "tenant_id": tenant_id, "as_of": as_of.isoformat(), "totals": {}, "invoices": []}

    async def partner_share_statement(self, tenant_id, partner_id, start, end):
        return {"schema": "volumeos.partner-share.v1", "tenant_id": tenant_id, "partner_id": partner_id, "period_start": start.isoformat(), "period_end": end.isoformat(), "receipt_count": 0, "totals": {}, "receipts": []}

    async def create_partner_contract(self, value):
        value["created_at"] = NOW.isoformat()
        value["effective_from"] = value["effective_from"].isoformat()
        return value

    async def list_partner_contracts(self, tenant_id, partner_id, limit):
        return []


class FakeReports:
    async def trial_balance(self, currency):
        return trial_balance()


def headers(tenant="tenant-a"):
    return {"Authorization": f"Bearer {TOKEN}", "X-VolumeOS-Tenant": tenant}


def test_report_api_exports_and_downloads_with_tenant_isolation():
    s3 = FakeS3()
    api = TestClient(create_app(FakeReportStore(), None, TOKEN, FakeReports(), FinancialExports("finance-archive", s3, now=lambda: NOW)))
    path = "/v1/tenants/tenant-a/reports/ar-aging/exports"
    created = api.post(path, headers=headers(), json={"as_of": "2026-10-28"})
    assert created.status_code == 200
    metadata = created.json()["export"]
    download = api.get(f"/v1/tenants/tenant-a/financial-exports/ar-aging/{metadata['sha256']}", headers=headers())
    assert download.status_code == 200
    assert download.headers["X-Object-Lock-Mode"] == "COMPLIANCE"
    denied = api.post(path, headers=headers("tenant-b"), json={"as_of": "2026-10-28"})
    assert denied.status_code == 403
    platform = api.get("/v1/tenants/platform/reports/trial-balance/NGN", headers=headers("platform"))
    assert platform.status_code == 200
    tenant_trial = api.get("/v1/tenants/tenant-a/reports/trial-balance/NGN", headers=headers())
    assert tenant_trial.status_code == 403


def test_partner_contract_and_statement_routes_validate_scope_and_period():
    api = TestClient(create_app(FakeReportStore(), None, TOKEN, FakeReports(), None))
    contract = api.post("/v1/tenants/tenant-a/partner-contracts", headers=headers(), json={
        "contract_id": "contract-1", "partner_id": "partner-1", "offer": "02", "basis_points": 2000,
        "effective_from": "2026-01-01T00:00:00Z",
    })
    assert contract.status_code == 201 and contract.json()["basis_points"] == 2000
    statement = api.get("/v1/tenants/tenant-a/reports/partner-share/partner-1?start=2026-01-01&end=2026-12-31", headers=headers())
    assert statement.status_code == 200 and statement.json()["receipt_count"] == 0
    invalid = api.get("/v1/tenants/tenant-a/reports/partner-share/partner-1?start=2026-12-31&end=2026-01-01", headers=headers())
    assert invalid.status_code == 400
