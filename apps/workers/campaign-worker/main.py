"""Durable campaign orchestration for VolumeOS M0 (FR-FLW-2/5/6).

External activities are deliberately fail-closed: no draft, reservation, dispatch,
or settlement is simulated. Every request carries an idempotency key and tenant scope.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

import metrics
from onboarding import (
    OnboardingWorkflow,
    activate_tenancy,
    compensate_onboarding,
    create_tenancy,
    initialize_listmonk,
    initialize_postiz,
    provision_billing_accounts,
    provision_identity,
    send_welcome,
)

PHONE_RE = re.compile(r"(?<!\d)(?:\+?234|0)[789][01]\d{8}(?!\d)")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")


def redact_pii(text: str) -> str:
    """Remove direct contact identifiers before an external AI call (FR-AI-8)."""
    return EMAIL_RE.sub("[redacted-email]", PHONE_RE.sub("[redacted-phone]", text))


@dataclass
class CampaignInput:
    tenant_id: str
    campaign_id: str
    brief: str
    amount_minor: int
    currency: str = "NGN"
    cycle_id: str = ""
    offer: str = "01"

    @property
    def ledger_event_id(self) -> str:
        digest = hashlib.blake2s(
            "\x1f".join((self.tenant_id, self.campaign_id, self.cycle_id)).encode(), digest_size=16
        ).hexdigest()
        return f"campaign:{digest}"


@dataclass(frozen=True)
class DispatchChannelResult:
    channel: str
    success: int
    failure: int


@dataclass(frozen=True)
class DispatchOutcome:
    dispatch_id: str
    cycle_id: str
    channels: list[DispatchChannelResult]


@dataclass
class Approval:
    approval_id: str
    approver: str


@dataclass(frozen=True)
class CampaignStatus:
    tenant_id: str
    campaign_id: str
    cycle_id: str
    status: str
    amount_minor: int
    currency: str
    channels: list[str]
    started_at: str
    updated_at: str


def validate_campaign(value: CampaignInput) -> None:
    if any(not ID_RE.fullmatch(identifier) for identifier in (value.tenant_id, value.campaign_id, value.cycle_id)):
        raise ApplicationError("valid tenant_id, campaign_id, and cycle_id are required", non_retryable=True)
    if value.amount_minor <= 0:
        raise ApplicationError("campaign budget must be positive", non_retryable=True)
    if value.currency not in {"NGN", "USD"}:
        raise ApplicationError("unsupported currency", non_retryable=True)
    if value.offer not in {f"{number:02}" for number in range(1, 9)}:
        raise ApplicationError("campaign offer must be one of 01 through 08", non_retryable=True)
    if not value.brief.strip():
        raise ApplicationError("campaign brief is required", non_retryable=True)


async def call(service_env: str, path: str, tenant_id: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
    base = os.environ.get(service_env, "").rstrip("/")
    if not base:
        raise ApplicationError(f"{service_env} is not configured", non_retryable=True)
    headers = {"Idempotency-Key": key, "X-VolumeOS-Tenant": tenant_id}
    if token := os.environ.get("VOLUMEOS_SERVICE_TOKEN", ""):
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        response = await client.post(base + path, json=payload, headers=headers)
        response.raise_for_status()
        result = response.json()
    if not isinstance(result, dict):
        raise ApplicationError(f"{service_env} returned an invalid response")
    return result


@activity.defn
async def draft_campaign(value: CampaignInput) -> str:
    activity.heartbeat()
    result = await call(
        "AI_GATEWAY_URL", "/v1/drafts", value.tenant_id,
        f"campaign:{value.campaign_id}:draft",
        {"campaign_id": value.campaign_id, "brief": redact_pii(value.brief)},
    )
    if not result.get("draft_id"):
        raise ApplicationError("AI gateway response is missing draft_id")
    return str(result["draft_id"])


@activity.defn
async def reserve_budget(value: CampaignInput) -> str:
    activity.heartbeat()
    result = await call(
        "BILLING_SERVICE_URL", f"/v1/tenants/{value.tenant_id}/charges/reserve", value.tenant_id,
        value.ledger_event_id,
        {
            "event_id": value.ledger_event_id,
            "amount_minor": str(value.amount_minor),
            "currency": value.currency,
            "kind": "campaign",
            "offer": value.offer,
            "channel": "campaign",
            "timeout_seconds": 900,
        },
    )
    if result.get("event_id") != value.ledger_event_id or result.get("state") != "pending":
        raise ApplicationError("ledger response does not match the campaign reservation")
    if result.get("tenant_id") != value.tenant_id or int(result.get("amount_minor", 0)) != value.amount_minor:
        raise ApplicationError("ledger response contains different campaign money fields", non_retryable=True)
    return value.ledger_event_id


def parse_dispatch_outcome(value: CampaignInput, result: dict[str, Any]) -> DispatchOutcome:
    dispatch_id = str(result.get("dispatch_id", ""))
    if not dispatch_id.strip():
        raise ApplicationError("dispatch response is missing dispatch_id")
    if result.get("cycle_id") != value.cycle_id:
        raise ApplicationError("dispatch response cycle_id does not match the requested cycle", non_retryable=True)
    raw_channels = result.get("channels")
    if not isinstance(raw_channels, list) or not raw_channels:
        raise ApplicationError("dispatch response is missing reconciled channel outcomes", non_retryable=True)
    channels: list[DispatchChannelResult] = []
    seen: set[str] = set()
    for raw in raw_channels:
        if not isinstance(raw, dict):
            raise ApplicationError("invalid channel outcome", non_retryable=True)
        channel = str(raw.get("channel", ""))
        success, failure = raw.get("success"), raw.get("failure")
        if channel not in metrics.CHANNELS or channel in seen:
            raise ApplicationError("invalid or duplicate dispatch channel", non_retryable=True)
        if any(isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in (success, failure)):
            raise ApplicationError("dispatch counts must be nonnegative integers", non_retryable=True)
        if success + failure == 0:
            raise ApplicationError("channel outcome must contain attempts", non_retryable=True)
        seen.add(channel)
        channels.append(DispatchChannelResult(channel, success, failure))
    return DispatchOutcome(dispatch_id, value.cycle_id, channels)


@activity.defn
async def dispatch_campaign(value: CampaignInput, draft_id: str, approval_id: str) -> DispatchOutcome:
    activity.heartbeat()
    result = await call(
        "CAMPAIGN_DISPATCH_URL", "/v1/dispatches", value.tenant_id,
        f"campaign:{value.campaign_id}:{value.cycle_id}:dispatch",
        {"campaign_id": value.campaign_id, "cycle_id": value.cycle_id, "draft_id": draft_id, "approval_id": approval_id},
    )
    return parse_dispatch_outcome(value, result)


@activity.defn
def record_campaign_approval(outcome: str) -> None:
    metrics.observe_approval(outcome)


@activity.defn
def record_campaign_cycle(value: CampaignInput, outcome: DispatchOutcome) -> None:
    if outcome.cycle_id != value.cycle_id:
        raise ApplicationError("cycle outcome does not match campaign", non_retryable=True)
    for channel in outcome.channels:
        metrics.record_cycle(
            value.tenant_id, value.campaign_id, value.cycle_id,
            channel.channel, channel.success, channel.failure,
        )


@activity.defn
async def settle_budget(value: CampaignInput, reservation_id: str, outcome: str) -> None:
    if outcome not in {"post", "void"}:
        raise ApplicationError("settlement must be post or void", non_retryable=True)
    if reservation_id != value.ledger_event_id:
        raise ApplicationError("settlement reservation does not belong to this campaign", non_retryable=True)
    result = await call(
        "BILLING_SERVICE_URL", f"/v1/tenants/{value.tenant_id}/charges/{reservation_id}/{outcome}", value.tenant_id,
        f"{reservation_id}:{outcome}", {},
    )
    if result.get("event_id") != reservation_id or result.get("tenant_id") != value.tenant_id:
        raise ApplicationError("ledger settlement scope changed", non_retryable=True)
    expected_state = "posted" if outcome == "post" else "voided"
    if result.get("state") != expected_state:
        raise ApplicationError("ledger settlement did not reach the requested state", non_retryable=True)


@workflow.defn
class CampaignWorkflow:
    def __init__(self) -> None:
        self._approval: Approval | None = None
        self._value: CampaignInput | None = None
        self._status = "drafting"
        self._channels: list[str] = []
        self._started_at = ""
        self._updated_at = ""

    def _transition(self, status: str) -> None:
        self._status = status
        self._updated_at = workflow.now().isoformat()

    @workflow.query
    def status(self) -> CampaignStatus:
        value = self._value
        return CampaignStatus(
            tenant_id=value.tenant_id if value else "",
            campaign_id=value.campaign_id if value else "",
            cycle_id=value.cycle_id if value else "",
            status=self._status,
            amount_minor=value.amount_minor if value else 0,
            currency=value.currency if value else "NGN",
            channels=list(self._channels),
            started_at=self._started_at,
            updated_at=self._updated_at,
        )

    @workflow.signal
    def approve(self, approval: Approval) -> None:
        if not approval.approval_id.strip() or not approval.approver.strip():
            raise ApplicationError("approval_id and approver are required")
        # Duplicate or out-of-state signals cannot replace the original human
        # decision or move a completed/timed-out workflow backwards.
        if self._status != "awaiting_approval" or self._approval is not None:
            return
        self._approval = approval
        self._transition("approved")

    @workflow.run
    async def run(self, value: CampaignInput) -> str:
        validate_campaign(value)
        self._value = value
        self._started_at = workflow.now().isoformat()
        self._updated_at = self._started_at
        activity_options = {
            "start_to_close_timeout": timedelta(minutes=2),
            "retry_policy": RetryPolicy(maximum_attempts=3),
        }
        try:
            draft_id = await workflow.execute_activity(draft_campaign, value, **activity_options)
            self._transition("awaiting_approval")
            try:
                await workflow.wait_condition(lambda: self._approval is not None, timeout=timedelta(hours=24))
            except asyncio.TimeoutError:
                self._transition("approval_timed_out")
                try:
                    await workflow.execute_activity(record_campaign_approval, "timeout", **activity_options)
                except Exception as exc:
                    workflow.logger.warning("approval metric recording failed: %s", exc)
                return "approval_timed_out"
            approval = self._approval
            assert approval is not None
            try:
                await workflow.execute_activity(record_campaign_approval, "approved", **activity_options)
            except Exception as exc:
                workflow.logger.warning("approval metric recording failed: %s", exc)
            self._transition("reserving")
            reservation_id = await workflow.execute_activity(reserve_budget, value, **activity_options)
            try:
                self._transition("dispatching")
                outcome = await workflow.execute_activity(
                    dispatch_campaign, args=[value, draft_id, approval.approval_id], **activity_options
                )
                self._channels = [channel.channel for channel in outcome.channels]
                try:
                    await workflow.execute_activity(record_campaign_cycle, args=[value, outcome], **activity_options)
                except Exception as exc:
                    # Metrics are operational side effects and must not void an
                    # already-dispatched campaign or change its financial outcome.
                    workflow.logger.warning("campaign cycle metric recording failed: %s", exc)
                self._transition("settling")
                await workflow.execute_activity(
                    settle_budget, args=[value, reservation_id, "post"], **activity_options
                )
            except BaseException:
                await workflow.execute_activity(
                    settle_budget, args=[value, reservation_id, "void"],
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=RetryPolicy(maximum_attempts=10),
                )
                raise
            self._transition("completed")
            return f"dispatched:{outcome.dispatch_id}"
        except asyncio.CancelledError:
            self._transition("cancelled")
            raise
        except BaseException:
            self._transition("failed")
            raise


async def main() -> None:
    metrics.start_server(int(os.environ.get("METRICS_PORT", "9091")))
    client = await Client.connect(
        os.environ.get("TEMPORAL_HOSTPORT", "temporal-frontend.ops.svc.cluster.local:7233"),
        namespace=os.environ.get("TEMPORAL_NAMESPACE", "volumeos"),
    )
    worker = Worker(
        client,
        task_queue=os.environ.get("TEMPORAL_TASK_QUEUE", "campaigns-v1"),
        workflows=[CampaignWorkflow, OnboardingWorkflow],
        activities=[
            draft_campaign, reserve_budget, dispatch_campaign, record_campaign_approval,
            record_campaign_cycle, settle_budget,
            provision_identity, create_tenancy, provision_billing_accounts,
            initialize_listmonk, initialize_postiz, activate_tenancy, send_welcome, compensate_onboarding,
        ],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
