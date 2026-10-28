"""Durable prepaid billing, dunning, usage, and FX workflows for VolumeOS M2.

TigerBeetle is the only balance store. PostgreSQL stores accounting documents and
workflow metadata, never a derived available balance. No activity simulates money
movement, message delivery, or tenant lifecycle success.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

with workflow.unsafe.imports_passed_through():
    import asyncpg
    import httpx

    from documents import invoice_draft, receipt_draft
    from models import (
        BillingActivitiesConfig,
        BillingCycleInput,
        BillingCycleResult,
        DocumentResult,
        DunningRecord,
        FxCloseInput,
        ReservationResult,
        UsagePostingResult,
        UsageRollup,
        UsageWeekStatus,
        WeeklyUsagePosting,
        calculate_fx_revaluation,
        deterministic_id,
        require_id,
    )
    from store import BillingStore, PostgresBillingStore


@dataclass(frozen=True)
class FundingRecovered:
    recovery_reference: str


@dataclass(frozen=True)
class BillingCycleStatus:
    tenant_id: str
    subscription_id: str
    cycle: str
    state: str
    dunning_attempts: int
    recovery_reference: str = ""


_store: BillingStore | None = None
_store_lock = asyncio.Lock()


async def get_store() -> BillingStore:
    global _store
    if _store is not None:
        return _store
    async with _store_lock:
        if _store is None:
            database_url = os.environ.get("DATABASE_URL", "")
            if not database_url:
                raise ApplicationError("DATABASE_URL is not configured", non_retryable=True)
            _store = await PostgresBillingStore.connect(database_url)
    return _store


def config() -> BillingActivitiesConfig:
    channels = tuple(item.strip() for item in os.environ.get("DUNNING_CHANNELS", "whatsapp,sms").split(",") if item.strip())
    if not channels or any(channel not in {"whatsapp", "sms", "email"} for channel in channels):
        raise ApplicationError("DUNNING_CHANNELS contains an unsupported channel", non_retryable=True)
    return BillingActivitiesConfig(messaging_channels=channels)


async def service_request(
    service_env: str,
    method: str,
    path: str,
    tenant_id: str,
    idempotency_key: str,
    payload: dict[str, Any] | None = None,
) -> httpx.Response:
    base = os.environ.get(service_env, "").rstrip("/")
    token = os.environ.get("VOLUMEOS_SERVICE_TOKEN", "")
    if not base or len(token) < 32:
        raise ApplicationError(f"{service_env} and a valid service token are required", non_retryable=True)
    headers = {
        "Authorization": f"Bearer {token}",
        "X-VolumeOS-Tenant": tenant_id,
        "Idempotency-Key": idempotency_key,
    }
    timeout = httpx.Timeout(config().request_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.request(method, base + path, json=payload, headers=headers)


def parse_reservation(response: httpx.Response, tenant_id: str, event_id: str) -> ReservationResult:
    body = response.json()
    if not isinstance(body, dict):
        raise ApplicationError("billing service returned a non-object reservation")
    result = ReservationResult(
        event_id=str(body.get("event_id", "")),
        tenant_id=str(body.get("tenant_id", "")),
        transfer_id=str(body.get("transfer_id", "")),
        amount_minor=int(body.get("amount_minor", 0)),
        state=str(body.get("state", "")),
    )
    if result.event_id != event_id or result.tenant_id != tenant_id:
        raise ApplicationError("billing reservation scope changed", non_retryable=True)
    if result.amount_minor <= 0 or not result.transfer_id.isdigit() or result.state not in {"pending", "posted", "voided"}:
        raise ApplicationError("billing reservation contains invalid money fields", non_retryable=True)
    return result


def parse_usage_posting(response: httpx.Response, value: WeeklyUsagePosting) -> UsagePostingResult:
    """Validate the complete usage/cost pair before recording its posting manifest."""
    body = response.json()
    if not isinstance(body, dict):
        raise ApplicationError("billing service returned a non-object usage posting")
    result = UsagePostingResult(
        event_id=str(body.get("event_id", "")),
        tenant_id=str(body.get("tenant_id", "")),
        transfer_id=str(body.get("transfer_id", "")),
        amount_minor=int(body.get("amount_minor", 0)),
        state=str(body.get("state", "")),
        provider_cost_minor=int(body.get("provider_cost_minor", -1)),
        provider_cost_transfer_id=str(body.get("provider_cost_transfer_id") or ""),
    )
    if (
        result.event_id != value.event_id
        or result.tenant_id != value.tenant_id
        or result.amount_minor != value.amount_minor
        or result.provider_cost_minor != value.provider_cost_minor
        or result.state != "posted"
        or not result.transfer_id.isdigit()
        or (value.provider_cost_minor > 0 and not result.provider_cost_transfer_id.isdigit())
        or (value.provider_cost_minor == 0 and result.provider_cost_transfer_id)
    ):
        raise ApplicationError("usage response does not match the closed week", non_retryable=True)
    return result


@activity.defn
async def issue_invoice(value: BillingCycleInput) -> str:
    value.validate()
    store = await get_store()
    return await store.record_invoice(invoice_draft(value))


@activity.defn
async def reserve_retainer(value: BillingCycleInput) -> ReservationResult | None:
    activity.heartbeat()
    response = await service_request(
        "BILLING_SERVICE_URL",
        "POST",
        f"/v1/tenants/{value.tenant_id}/charges/reserve",
        value.tenant_id,
        value.event_id,
        {
            "event_id": value.event_id,
            "amount_minor": str(value.total_minor),
            "currency": value.currency,
            "kind": "retainer",
            "offer": value.offer,
            "timeout_seconds": config().reserve_timeout_seconds,
        },
    )
    if response.status_code == 402:
        return None
    response.raise_for_status()
    result = parse_reservation(response, value.tenant_id, value.event_id)
    if result.state != "pending" or result.amount_minor != value.total_minor:
        raise ApplicationError("reserve response does not match the invoice", non_retryable=True)
    return result


@activity.defn
async def post_retainer(value: BillingCycleInput) -> ReservationResult:
    activity.heartbeat()
    response = await service_request(
        "BILLING_SERVICE_URL",
        "POST",
        f"/v1/tenants/{value.tenant_id}/charges/{value.event_id}/post",
        value.tenant_id,
        f"{value.event_id}:post",
        {},
    )
    response.raise_for_status()
    result = parse_reservation(response, value.tenant_id, value.event_id)
    if result.state != "posted" or result.amount_minor != value.total_minor:
        raise ApplicationError("post response does not match the invoice", non_retryable=True)
    return result


@activity.defn
async def append_receipt(value: BillingCycleInput, reservation: ReservationResult) -> DocumentResult:
    invoice = invoice_draft(value)
    receipt = receipt_draft(invoice, reservation)
    store = await get_store()
    return await store.record_receipt(invoice, receipt)


@activity.defn
async def deliver_document(value: BillingCycleInput, kind: str, document_id: str) -> None:
    """Deliver immutable PDFs; provider acceptance is not device delivery."""
    if kind not in {"invoice", "receipt"}:
        raise ApplicationError("unsupported billing document kind", non_retryable=True)
    response = await service_request(
        "DOCUMENT_SERVICE_URL",
        "POST",
        f"/v1/tenants/{value.tenant_id}/documents/{kind}/{document_id}/deliveries",
        value.tenant_id,
        deterministic_id("document-delivery", value.tenant_id, kind, document_id, value.billing_whatsapp),
        {"channel": "whatsapp", "recipient": value.billing_whatsapp},
    )
    response.raise_for_status()
    body = response.json()
    if (
        body.get("status") != "provider_accepted"
        or body.get("tenant_id") != value.tenant_id
        or body.get("document_id") != document_id
        or body.get("document_kind") != kind
        or not str(body.get("provider_message_id", "")).strip()
    ):
        raise ApplicationError("document delivery receipt is invalid", non_retryable=True)


@activity.defn
async def record_dunning_state(record: DunningRecord) -> None:
    store = await get_store()
    await store.record_dunning(record)


@activity.defn
async def send_dunning_nudge(value: BillingCycleInput, attempt: int) -> None:
    if attempt < 1 or attempt > len(value.dunning_delays_seconds):
        raise ApplicationError("invalid dunning attempt", non_retryable=True)
    for channel in config().messaging_channels:
        response = await service_request(
            "MESSAGING_URL",
            "POST",
            "/v1/dunning-nudges",
            value.tenant_id,
            deterministic_id("dunning", value.tenant_id, value.subscription_id, value.cycle, str(attempt), channel),
            {
                "channel": channel,
                "subscription_id": value.subscription_id,
                "cycle": value.cycle,
                "attempt": attempt,
                "amount_minor": str(value.total_minor),
                "currency": value.currency,
            },
        )
        response.raise_for_status()


@activity.defn
async def set_tenant_paused(value: BillingCycleInput, paused: bool) -> None:
    """Pause/restore with a fresh optimistic version from the tenancy service."""
    current = await service_request(
        "TENANCY_URL", "GET", "/v1/tenants/current", value.tenant_id,
        deterministic_id("billing-read", value.tenant_id, value.subscription_id, value.cycle, str(paused)),
    )
    current.raise_for_status()
    tenant = current.json()
    target = "paused" if paused else "active"
    if tenant.get("state") == target:
        return
    if tenant.get("state") == "offboarded":
        raise ApplicationError("an offboarded tenant cannot be restored by billing", non_retryable=True)
    version = tenant.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ApplicationError("tenancy response is missing a valid version", non_retryable=True)
    response = await service_request(
        "TENANCY_URL",
        "POST",
        "/v1/tenants/current/transitions",
        value.tenant_id,
        deterministic_id("billing-transition", value.tenant_id, value.subscription_id, value.cycle, target),
        {
            "to": target,
            "version": version,
            "reason": f"prepaid billing {value.cycle}: {'dunning exhausted' if paused else 'confirmed funds recovered'}",
        },
    )
    response.raise_for_status()


@activity.defn
async def post_usage_rollup(value: WeeklyUsagePosting) -> UsagePostingResult:
    value.validate()
    response = await service_request(
        "BILLING_SERVICE_URL",
        "POST",
        f"/v1/tenants/{value.tenant_id}/usage",
        value.tenant_id,
        value.event_id,
        {
            "event_id": value.event_id,
            "amount_minor": str(value.amount_minor),
            "provider_cost_minor": str(value.provider_cost_minor),
            "currency": value.currency,
            "channel": value.channel,
            "provider": value.provider,
            "offer": value.offer,
            "corridor": value.campaign_id,
            "timeout_seconds": config().reserve_timeout_seconds,
        },
    )
    response.raise_for_status()
    result = parse_usage_posting(response, value)
    store = await get_store()
    await store.record_usage(value, result)
    return result


@activity.defn
async def close_fx_month(value: FxCloseInput) -> str:
    revaluation = calculate_fx_revaluation(value)
    transfer_id: str | None = None
    if revaluation.amount_minor:
        response = await service_request(
            "BILLING_SERVICE_URL",
            "POST",
            "/v1/tenants/platform/fx/revalue",
            "platform",
            value.event_id,
            {
                "event_id": value.event_id,
                "provider": value.provider,
                "amount_minor": str(revaluation.amount_minor),
                "direction": revaluation.direction,
            },
        )
        response.raise_for_status()
        body = response.json()
        transfer_id = str(body.get("transfer_id", ""))
        if not transfer_id.isdigit() or int(body.get("amount_minor", 0)) != revaluation.amount_minor:
            raise ApplicationError("FX response contains invalid money fields", non_retryable=True)
    store = await get_store()
    await store.record_fx(value, revaluation, transfer_id)
    return transfer_id or "no-revaluation"


@workflow.defn
class BillingCycleWorkflow:
    def __init__(self) -> None:
        self._value: BillingCycleInput | None = None
        self._state = "issuing"
        self._attempts = 0
        self._recovery: FundingRecovered | None = None
        self._recovery_reference = ""

    @workflow.query
    def status(self) -> BillingCycleStatus:
        value = self._value
        return BillingCycleStatus(
            tenant_id=value.tenant_id if value else "",
            subscription_id=value.subscription_id if value else "",
            cycle=value.cycle if value else "",
            state=self._state,
            dunning_attempts=self._attempts,
            recovery_reference=self._recovery_reference,
        )

    @workflow.signal
    def funding_recovered(self, signal: FundingRecovered) -> None:
        if self._state not in {"awaiting_funds", "paused"}:
            return
        require_id("recovery_reference", signal.recovery_reference)
        self._recovery = signal
        self._recovery_reference = signal.recovery_reference

    def _transition(self, state: str) -> None:
        self._state = state

    async def _record_dunning(self, value: BillingCycleInput, state: str) -> None:
        await workflow.execute_activity(
            record_dunning_state,
            DunningRecord(value.tenant_id, value.subscription_id, value.cycle, state, self._attempts, self._recovery_reference),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=RetryPolicy(maximum_attempts=5),
        )

    @workflow.run
    async def run(self, value: BillingCycleInput) -> BillingCycleResult:
        value.validate()
        self._value = value
        activity_options = {
            "start_to_close_timeout": timedelta(minutes=2),
            "retry_policy": RetryPolicy(maximum_attempts=5),
        }
        invoice_number = await workflow.execute_activity(issue_invoice, value, **activity_options)
        invoice_document_id = deterministic_id("invoice", value.tenant_id, value.subscription_id, value.cycle)
        await workflow.execute_activity(
            deliver_document, args=[value, "invoice", invoice_document_id], **activity_options
        )
        paused = False
        while True:
            self._transition("reserving")
            reservation = await workflow.execute_activity(reserve_retainer, value, **activity_options)
            if reservation is not None:
                break
            self._transition("paused" if paused else "awaiting_funds")
            if not paused and self._attempts < len(value.dunning_delays_seconds):
                self._attempts += 1
                await self._record_dunning(value, "nudging")
                await workflow.execute_activity(
                    send_dunning_nudge, args=[value, self._attempts], **activity_options
                )
                delay = value.dunning_delays_seconds[self._attempts - 1]
                try:
                    await workflow.wait_condition(lambda: self._recovery is not None, timeout=timedelta(seconds=delay))
                except asyncio.TimeoutError:
                    continue
            else:
                if not paused:
                    await workflow.execute_activity(
                        set_tenant_paused, args=[value, True], **activity_options
                    )
                    paused = True
                    self._transition("paused")
                    await self._record_dunning(value, "paused")
                await workflow.wait_condition(lambda: self._recovery is not None)
            recovery = self._recovery
            assert recovery is not None
            self._recovery = None
            if paused:
                await workflow.execute_activity(
                    set_tenant_paused, args=[value, False], **activity_options
                )
                paused = False
            self._transition("recovering")
        if self._attempts:
            await self._record_dunning(value, "recovered")
        self._transition("posting")
        posted = await workflow.execute_activity(post_retainer, value, **activity_options)
        self._transition("receipting")
        documents = await workflow.execute_activity(
            append_receipt, args=[value, posted], **activity_options
        )
        await workflow.execute_activity(
            deliver_document, args=[value, "receipt", documents.receipt_id], **activity_options
        )
        self._transition("settled")
        return BillingCycleResult(
            value.tenant_id,
            value.subscription_id,
            value.cycle,
            "settled",
            posted.transfer_id,
            invoice_number or documents.invoice_number,
            documents.receipt_number,
        )


@workflow.defn
class UsageRollupWorkflow:
    """Collect immutable closed days, then post one deterministic ISO-week charge."""

    def __init__(self) -> None:
        self._state = "collecting"
        self._days: list[UsageRollup] = []

    @workflow.query
    def status(self) -> UsageWeekStatus:
        """Expose every accepted day so dispatcher replay can detect conflicts."""
        return UsageWeekStatus(self._state, list(self._days))

    @workflow.signal
    def record_day(self, value: UsageRollup) -> None:
        value.validate()
        if self._state != "collecting" or not self._days:
            raise ApplicationError("usage week is already closed", non_retryable=True)
        if not self._days[0].same_week_scope(value):
            raise ApplicationError("usage day changed the week billing scope", non_retryable=True)
        for existing in self._days:
            if existing.day == value.day:
                if existing != value:
                    raise ApplicationError("closed usage day changed after dispatch", non_retryable=True)
                return
        self._days.append(value)

    @workflow.run
    async def run(self, value: UsageRollup) -> UsagePostingResult:
        value.validate()
        self._days = [value]
        close_delay = value.close_time() - workflow.now()
        if close_delay.total_seconds() > 0:
            await workflow.sleep(close_delay)
        self._state = "posting"
        posting = WeeklyUsagePosting.from_days(self._days)
        result = await workflow.execute_activity(
            post_usage_rollup,
            posting,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=5),
        )
        self._state = "posted"
        return result


@workflow.defn
class FxCloseWorkflow:
    @workflow.run
    async def run(self, value: FxCloseInput) -> str:
        return await workflow.execute_activity(
            close_fx_month,
            value,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=5),
        )


async def main() -> None:
    client = await Client.connect(
        os.environ.get("TEMPORAL_HOSTPORT", "temporal-frontend.ops.svc.cluster.local:7233"),
        namespace=os.environ.get("TEMPORAL_NAMESPACE", "volumeos"),
    )
    worker = Worker(
        client,
        task_queue=os.environ.get("TEMPORAL_TASK_QUEUE", "billing-v1"),
        workflows=[BillingCycleWorkflow, UsageRollupWorkflow, FxCloseWorkflow],
        activities=[
            issue_invoice,
            reserve_retainer,
            post_retainer,
            append_receipt,
            deliver_document,
            record_dunning_state,
            send_dunning_nudge,
            set_tenant_paused,
            post_usage_rollup,
            close_fx_month,
        ],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
