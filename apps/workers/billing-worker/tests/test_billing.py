import asyncio

import httpx
import pytest
from temporalio.exceptions import ApplicationError

import billing_main as billing
from documents import invoice_draft, invoice_fields, receipt_draft, render_invoice_pdf
from models import (
    BillingCycleInput,
    DocumentResult,
    DunningRecord,
    FxCloseInput,
    Party,
    ReservationResult,
    UsagePostingResult,
    UsageRollup,
    WeeklyUsagePosting,
    calculate_fx_revaluation,
)


def cycle(delays=(1, 2)):
    return BillingCycleInput(
        tenant_id="tenant-a",
        subscription_id="subscription-1",
        offer="02",
        cycle="2026-10",
        issue_date="2026-10-01",
        retainer_minor=5_500_000,
        setup_minor=0,
        usage_minor=125_000,
        tax_minor=0,
        currency="NGN",
        seller=Party("VolumeOS Operator", "1234567890", "1 Operator Way, Lagos"),
        buyer=Party("Test Customer", "0987654321", "2 Customer Street, Abuja"),
        billing_whatsapp="+2348012345678",
        tenant_version=3,
        dunning_delays_seconds=delays,
    )


def reservation(state="posted"):
    value = cycle()
    return ReservationResult(value.event_id, value.tenant_id, "123456789", value.total_minor, state)


def usage(day="2026-10-28", amount=12500, cost=9000):
    return UsageRollup(
        "tenant-a", "termii", "sms", "campaign-1", "02", day,
        25, amount, cost, "NGN",
    )


def posting_result(posting):
    return UsagePostingResult(
        posting.event_id, posting.tenant_id, "999", posting.amount_minor,
        "posted", posting.provider_cost_minor, "1001" if posting.provider_cost_minor else "",
    )


class FakeStore:
    def __init__(self):
        self.dunning = []
        self.usage = []
        self.fx = []

    async def record_dunning(self, record):
        self.dunning.append(record)

    async def record_usage(self, rollup, transfer_id):
        self.usage.append((rollup, transfer_id))

    async def record_fx(self, value, revaluation, transfer_id):
        self.fx.append((value, revaluation, transfer_id))


def test_cycle_invoice_and_receipt_use_exact_minor_units():
    value = cycle()
    assert value.subtotal_minor == 5_625_000
    assert value.total_minor == 5_625_000
    assert value.event_id == cycle().event_id
    invoice = invoice_draft(value)
    assert invoice.total_minor == value.total_minor
    assert invoice_fields(invoice)["total_minor"] == str(value.total_minor)
    assert render_invoice_pdf(invoice).startswith(b"%PDF")
    receipt = receipt_draft(invoice, reservation())
    assert receipt.amount_minor == invoice.total_minor
    with pytest.raises(ValueError):
        receipt_draft(invoice, reservation("pending"))
    with pytest.raises(ValueError):
        receipt_draft(invoice, ReservationResult(value.event_id, value.tenant_id, "1", 1, "posted"))


def test_cycle_validation_rejects_invalid_money_and_period():
    value = cycle()
    object.__setattr__(value, "retainer_minor", 0)
    with pytest.raises(ApplicationError):
        value.validate()
    with pytest.raises(ApplicationError):
        BillingCycleInput(**{**value.__dict__, "retainer_minor": 1, "issue_date": "2026-11-01"}).validate()


def test_usage_and_fx_calculations_are_deterministic_and_exact():
    value = usage()
    value.validate()
    assert value.event_id == usage().event_id
    week = WeeklyUsagePosting.from_days([value, usage("2026-10-29", 2500, 1000)])
    assert week.event_count == 50
    assert week.amount_minor == 15000
    assert week.provider_cost_minor == 10000
    assert week.event_id == value.event_id
    loss = calculate_fx_revaluation(FxCloseInput("platform", "openai", "2026-10", 12345, 150000, 160000))
    assert loss.amount_minor == 1_234_500
    assert loss.direction == "loss"
    gain = calculate_fx_revaluation(FxCloseInput("platform", "openai", "2026-10", 12345, 160000, 150000))
    assert gain.direction == "gain"
    unchanged = calculate_fx_revaluation(FxCloseInput("platform", "openai", "2026-10", 12345, 150000, 150000))
    assert unchanged.amount_minor == 0


def test_reservation_parser_rejects_cross_tenant_or_wrong_state_fields():
    value = cycle()
    response = httpx.Response(200, json={
        "event_id": value.event_id, "tenant_id": "tenant-b", "transfer_id": "1",
        "amount_minor": str(value.total_minor), "state": "pending",
    })
    with pytest.raises(ApplicationError):
        billing.parse_reservation(response, value.tenant_id, value.event_id)


async def test_successful_billing_cycle_posts_before_receipt(monkeypatch):
    value = cycle()
    calls = []

    async def execute_activity(activity, args=None, **_kwargs):
        arguments = args if isinstance(args, list) else [args]
        calls.append(activity)
        if activity is billing.issue_invoice:
            return "INV-2026-000001"
        if activity is billing.reserve_retainer:
            return reservation("pending")
        if activity is billing.post_retainer:
            return reservation("posted")
        if activity is billing.deliver_document:
            assert arguments[2] in {"invoice-1", "receipt-1"} or arguments[1] == "invoice"
            return None
        if activity is billing.append_receipt:
            assert arguments[1].state == "posted"
            return DocumentResult("invoice-1", "INV-2026-000001", "receipt-1", "RCT-2026-000001")
        raise AssertionError(f"unexpected activity {activity}")

    monkeypatch.setattr(billing.workflow, "execute_activity", execute_activity)
    instance = billing.BillingCycleWorkflow()
    result = await instance.run(value)
    assert result.status == "settled"
    assert result.receipt_number == "RCT-2026-000001"
    assert calls.index(billing.post_retainer) < calls.index(billing.append_receipt)
    assert instance.status().state == "settled"


async def test_dunning_pauses_and_recovers_with_fresh_signal(monkeypatch):
    value = cycle(delays=(1, 2))
    reserve_calls = 0
    transitions = []
    instance = billing.BillingCycleWorkflow()

    async def execute_activity(activity, args=None, **_kwargs):
        nonlocal reserve_calls
        arguments = args if isinstance(args, list) else [args]
        if activity is billing.issue_invoice:
            return "INV-2026-000001"
        if activity is billing.reserve_retainer:
            reserve_calls += 1
            return reservation("pending") if reserve_calls == 4 else None
        if activity is billing.set_tenant_paused:
            transitions.append(arguments[1])
            return None
        if activity is billing.record_dunning_state:
            return None
        if activity is billing.send_dunning_nudge:
            return None
        if activity is billing.post_retainer:
            return reservation("posted")
        if activity is billing.deliver_document:
            assert arguments[2] in {"invoice-1", "receipt-1"} or arguments[1] == "invoice"
            return None
        if activity is billing.append_receipt:
            return DocumentResult("invoice-1", "INV-2026-000001", "receipt-1", "RCT-2026-000001")
        raise AssertionError(f"unexpected activity {activity}")

    async def wait_condition(_condition, timeout=None, **_kwargs):
        if timeout is not None:
            raise asyncio.TimeoutError
        instance.funding_recovered(billing.FundingRecovered("collection:confirmed-1"))

    monkeypatch.setattr(billing.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(billing.workflow, "wait_condition", wait_condition)
    result = await instance.run(value)
    assert result.status == "settled"
    assert reserve_calls == 4
    assert transitions == [True, False]
    assert instance.status().dunning_attempts == 2
    assert instance.status().recovery_reference == "collection:confirmed-1"


def test_recovery_signal_is_state_gated_and_does_not_replace_reference(monkeypatch):
    instance = billing.BillingCycleWorkflow()
    instance._state = "reserving"
    instance.funding_recovered(billing.FundingRecovered("collection:early"))
    assert instance._recovery is None
    instance._state = "paused"
    instance.funding_recovered(billing.FundingRecovered("collection:valid"))
    assert instance._recovery_reference == "collection:valid"
    with pytest.raises(ApplicationError):
        instance.funding_recovered(billing.FundingRecovered("bad reference with spaces"))


async def test_usage_and_fx_activities_persist_only_confirmed_postings(monkeypatch):
    store = FakeStore()
    captured = []

    async def fake_store():
        return store

    async def request(_service, _method, _path, _tenant, _key, payload=None):
        if "usage" in _path:
            captured.append(payload)
            return httpx.Response(200, request=httpx.Request("POST", "http://billing.test/usage"), json={
                "event_id": payload["event_id"], "tenant_id": _tenant, "transfer_id": "999",
                "amount_minor": payload["amount_minor"], "state": "posted",
                "provider_cost_minor": payload["provider_cost_minor"],
                "provider_cost_transfer_id": "1001",
            })
        return httpx.Response(200, request=httpx.Request("POST", "http://billing.test/fx"), json={"transfer_id": "1000", "amount_minor": payload["amount_minor"]})

    monkeypatch.setattr(billing, "get_store", fake_store)
    monkeypatch.setattr(billing, "service_request", request)
    posting = WeeklyUsagePosting.from_days([usage()])
    result = await billing.post_usage_rollup(posting)
    assert result.transfer_id == "999"
    assert result.provider_cost_transfer_id == "1001"
    assert captured[0]["provider"] == "termii"
    assert captured[0]["offer"] == "02"
    assert captured[0]["provider_cost_minor"] == "9000"
    assert store.usage[0] == (posting, result)
    fx = FxCloseInput("platform", "openai", "2026-10", 10000, 150000, 160000)
    assert await billing.close_fx_month(fx) == "1000"
    assert store.fx[0][2] == "1000"


async def test_usage_workflow_collects_closed_days_and_posts_one_week(monkeypatch):
    first = usage()
    second = usage("2026-10-29", 2500, 1000)
    instance = billing.UsageRollupWorkflow()
    slept = []

    async def execute_activity(activity, value, **_kwargs):
        assert activity is billing.post_usage_rollup
        assert value == WeeklyUsagePosting.from_days([first, second])
        return posting_result(value)

    async def sleep(delay):
        slept.append(delay)
        instance.record_day(second)

    monkeypatch.setattr(billing.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(billing.workflow, "sleep", sleep)
    monkeypatch.setattr(billing.workflow, "now", lambda: first.close_time() - billing.timedelta(hours=1))
    result = await instance.run(first)
    assert slept == [billing.timedelta(hours=1)]
    assert result.amount_minor == 15000
    assert result.provider_cost_minor == 10000
    assert instance.status().state == "posted"
    assert instance.status().days == [first, second]


def test_usage_signal_rejects_changed_day_and_cross_scope_replay():
    first = usage()
    instance = billing.UsageRollupWorkflow()
    instance._days = [first]
    instance.record_day(first)
    assert instance.status().days == [first]
    with pytest.raises(ApplicationError, match="changed after dispatch"):
        instance.record_day(usage(amount=12501))
    with pytest.raises(ApplicationError, match="billing scope"):
        instance.record_day(UsageRollup(
            "tenant-b", "termii", "sms", "campaign-1", "02", "2026-10-29",
            25, 2500, 1000, "NGN",
        ))
    instance._state = "posted"
    with pytest.raises(ApplicationError, match="already closed"):
        instance.record_day(usage("2026-10-29", 2500, 1000))
