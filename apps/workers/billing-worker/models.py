"""Validated billing contracts and exact minor-unit calculations."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from temporalio.exceptions import ApplicationError

Currency = Literal["NGN", "USD"]
SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
CYCLE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
TIN = re.compile(r"^\d{8,14}$")
CHANNELS = {"sms", "whatsapp", "email", "social", "ai", "gpu"}


def require_id(name: str, value: str) -> None:
    if not SAFE_ID.fullmatch(value):
        raise ApplicationError(f"valid {name} is required", non_retryable=True)


def require_currency(value: str) -> None:
    if value not in {"NGN", "USD"}:
        raise ApplicationError("currency must be NGN or USD", non_retryable=True)


def require_tin(name: str, value: str) -> None:
    if not TIN.fullmatch(value):
        raise ApplicationError(f"{name} must contain 8-14 digits", non_retryable=True)


def deterministic_id(prefix: str, *parts: str) -> str:
    digest = hashlib.blake2s("\x1f".join(parts).encode(), digest_size=16).hexdigest()
    return f"{prefix}:{digest}"


@dataclass(frozen=True)
class Party:
    legal_name: str
    tin: str
    address: str

    def validate(self, role: str) -> None:
        if not 2 <= len(self.legal_name.strip()) <= 160 or not 2 <= len(self.address.strip()) <= 300:
            raise ApplicationError(f"valid {role} legal name and address are required", non_retryable=True)
        require_tin(f"{role} TIN", self.tin)


@dataclass(frozen=True)
class BillingCycleInput:
    tenant_id: str
    subscription_id: str
    offer: str
    cycle: str
    issue_date: str
    retainer_minor: int
    setup_minor: int
    usage_minor: int
    tax_minor: int
    currency: Currency
    seller: Party
    buyer: Party
    billing_whatsapp: str
    tenant_version: int
    dunning_delays_seconds: tuple[int, ...] = (3 * 86400, 7 * 86400, 14 * 86400)

    @property
    def subtotal_minor(self) -> int:
        return self.retainer_minor + self.setup_minor + self.usage_minor

    @property
    def total_minor(self) -> int:
        return self.subtotal_minor + self.tax_minor

    @property
    def event_id(self) -> str:
        return deterministic_id("retainer", self.tenant_id, self.subscription_id, self.cycle)

    def validate(self) -> None:
        require_id("tenant_id", self.tenant_id)
        require_id("subscription_id", self.subscription_id)
        if self.offer not in {f"{number:02}" for number in range(1, 9)}:
            raise ApplicationError("offer must be one of 01 through 08", non_retryable=True)
        if not CYCLE.fullmatch(self.cycle):
            raise ApplicationError("cycle must use YYYY-MM", non_retryable=True)
        try:
            parsed_issue = date.fromisoformat(self.issue_date)
        except ValueError as error:
            raise ApplicationError("issue_date must be an ISO date", non_retryable=True) from error
        if parsed_issue.strftime("%Y-%m") != self.cycle:
            raise ApplicationError("issue_date must fall inside the billing cycle", non_retryable=True)
        require_currency(self.currency)
        if self.retainer_minor <= 0 or min(self.setup_minor, self.usage_minor, self.tax_minor) < 0:
            raise ApplicationError("retainer must be positive and other amounts nonnegative", non_retryable=True)
        if self.total_minor <= 0 or self.tenant_version < 1:
            raise ApplicationError("invalid billing total or tenant version", non_retryable=True)
        if not 1 <= len(self.dunning_delays_seconds) <= 5 or any(delay < 1 for delay in self.dunning_delays_seconds):
            raise ApplicationError("dunning policy requires 1-5 positive delays", non_retryable=True)
        self.seller.validate("seller")
        self.buyer.validate("buyer")
        if not re.fullmatch(r"^\+[1-9]\d{7,14}$", self.billing_whatsapp):
            raise ApplicationError("billing_whatsapp must be an E.164 number", non_retryable=True)


@dataclass(frozen=True)
class UsageRollup:
    """One immutable closed day collected by its ISO-week workflow."""

    tenant_id: str
    provider: str
    channel: str
    campaign_id: str
    offer: str
    day: str
    event_count: int
    amount_minor: int
    provider_cost_minor: int
    currency: Currency
    close_delay_seconds: int = 172_800

    @property
    def week(self) -> str:
        iso = date.fromisoformat(self.day).isocalendar()
        return f"{iso.year}-W{iso.week:02d}"

    @property
    def event_id(self) -> str:
        return deterministic_id(
            "usage", self.tenant_id, self.provider, self.channel,
            self.campaign_id, self.offer, self.week,
        )

    def close_time(self) -> datetime:
        current = date.fromisoformat(self.day)
        following_week = current + timedelta(days=7 - current.weekday())
        return datetime.combine(following_week, time.min, tzinfo=timezone.utc) + timedelta(
            seconds=self.close_delay_seconds
        )

    def validate(self) -> None:
        require_id("tenant_id", self.tenant_id)
        require_id("provider", self.provider)
        require_id("campaign_id", self.campaign_id)
        if self.channel not in CHANNELS:
            raise ApplicationError("unsupported usage channel", non_retryable=True)
        if self.offer not in {f"{number:02}" for number in range(1, 9)}:
            raise ApplicationError("usage offer must be one of 01 through 08", non_retryable=True)
        try:
            date.fromisoformat(self.day)
        except ValueError as error:
            raise ApplicationError("usage day must be an ISO date", non_retryable=True) from error
        require_currency(self.currency)
        if self.event_count <= 0 or self.amount_minor <= 0 or self.provider_cost_minor < 0:
            raise ApplicationError("usage count and amount must be positive and cost nonnegative", non_retryable=True)
        if not 3_600 <= self.close_delay_seconds <= 604_800:
            raise ApplicationError("usage close delay must be between 1 and 168 hours", non_retryable=True)

    def same_week_scope(self, other: "UsageRollup") -> bool:
        return (
            self.event_id == other.event_id
            and self.currency == other.currency
            and self.close_delay_seconds == other.close_delay_seconds
        )


@dataclass(frozen=True)
class WeeklyUsagePosting:
    tenant_id: str
    provider: str
    channel: str
    campaign_id: str
    offer: str
    week: str
    first_day: str
    last_day: str
    event_count: int
    amount_minor: int
    provider_cost_minor: int
    currency: Currency

    @classmethod
    def from_days(cls, days: list[UsageRollup]) -> "WeeklyUsagePosting":
        if not days:
            raise ApplicationError("a usage week requires at least one closed day", non_retryable=True)
        first = days[0]
        first.validate()
        if any(not first.same_week_scope(day) for day in days[1:]):
            raise ApplicationError("usage days do not belong to the same billing week", non_retryable=True)
        for day in days[1:]:
            day.validate()
        ordered = sorted(days, key=lambda day: day.day)
        if len({day.day for day in ordered}) != len(ordered):
            raise ApplicationError("usage week contains a duplicate billing day", non_retryable=True)
        return cls(
            first.tenant_id, first.provider, first.channel, first.campaign_id,
            first.offer, first.week, ordered[0].day, ordered[-1].day,
            sum(day.event_count for day in ordered),
            sum(day.amount_minor for day in ordered),
            sum(day.provider_cost_minor for day in ordered), first.currency,
        )

    @property
    def event_id(self) -> str:
        return deterministic_id(
            "usage", self.tenant_id, self.provider, self.channel,
            self.campaign_id, self.offer, self.week,
        )

    def validate(self) -> None:
        require_id("tenant_id", self.tenant_id)
        require_id("provider", self.provider)
        require_id("campaign_id", self.campaign_id)
        if self.channel not in CHANNELS or self.offer not in {f"{number:02}" for number in range(1, 9)}:
            raise ApplicationError("unsupported usage channel or offer", non_retryable=True)
        require_currency(self.currency)
        try:
            first = date.fromisoformat(self.first_day)
            last = date.fromisoformat(self.last_day)
            iso = first.isocalendar()
        except ValueError as error:
            raise ApplicationError("usage week days must be ISO dates", non_retryable=True) from error
        if self.week != f"{iso.year}-W{iso.week:02d}" or last < first or (last - first).days > 6:
            raise ApplicationError("usage week boundaries are invalid", non_retryable=True)
        if self.event_count <= 0 or self.amount_minor <= 0 or self.provider_cost_minor < 0:
            raise ApplicationError("weekly usage count and amount must be positive and cost nonnegative", non_retryable=True)


@dataclass(frozen=True)
class UsageWeekStatus:
    state: str
    days: list[UsageRollup]


@dataclass(frozen=True)
class UsagePostingResult:
    event_id: str
    tenant_id: str
    transfer_id: str
    amount_minor: int
    state: str
    provider_cost_minor: int
    provider_cost_transfer_id: str


@dataclass(frozen=True)
class DunningInput:
    tenant_id: str
    subscription_id: str
    cycle: str
    tenant_version: int
    delays_seconds: tuple[int, ...] = (3 * 86400, 7 * 86400, 14 * 86400)


@dataclass(frozen=True)
class DunningStatus:
    tenant_id: str
    subscription_id: str
    cycle: str
    state: str
    attempts: int
    recovery_reference: str = ""


@dataclass(frozen=True)
class FxCloseInput:
    tenant_id: str
    provider: str
    month: str
    usd_minor: int
    previous_ngn_minor_per_usd: int
    closing_ngn_minor_per_usd: int

    @property
    def event_id(self) -> str:
        return deterministic_id("fx-close", self.provider, self.month)

    def validate(self) -> None:
        if self.tenant_id != "platform":
            raise ApplicationError("FX close requires the platform scope", non_retryable=True)
        require_id("provider", self.provider)
        if not CYCLE.fullmatch(self.month):
            raise ApplicationError("FX month must use YYYY-MM", non_retryable=True)
        if min(self.usd_minor, self.previous_ngn_minor_per_usd, self.closing_ngn_minor_per_usd) <= 0:
            raise ApplicationError("FX amount and rates must be positive", non_retryable=True)


@dataclass(frozen=True)
class FxRevaluation:
    amount_minor: int
    direction: Literal["gain", "loss"]


def calculate_fx_revaluation(value: FxCloseInput) -> FxRevaluation:
    """Revalue USD cents using integer kobo-per-USD rates, rounding half-up."""
    value.validate()
    rate_delta = value.closing_ngn_minor_per_usd - value.previous_ngn_minor_per_usd
    if rate_delta == 0:
        return FxRevaluation(0, "gain")
    # USD minor units are cents; one USD is 100 cents. All inputs are integers.
    numerator = abs(rate_delta) * value.usd_minor
    amount = (numerator + 50) // 100
    return FxRevaluation(amount, "loss" if rate_delta > 0 else "gain")


@dataclass(frozen=True)
class ReservationResult:
    event_id: str
    tenant_id: str
    transfer_id: str
    amount_minor: int
    state: str


@dataclass(frozen=True)
class DocumentResult:
    invoice_id: str
    invoice_number: str
    receipt_id: str
    receipt_number: str


@dataclass(frozen=True)
class BillingCycleResult:
    tenant_id: str
    subscription_id: str
    cycle: str
    status: str
    transfer_id: str = ""
    invoice_number: str = ""
    receipt_number: str = ""


@dataclass(frozen=True)
class DunningRecord:
    tenant_id: str
    subscription_id: str
    cycle: str
    state: str
    attempts: int
    recovery_reference: str = ""


@dataclass(frozen=True)
class BillingActivitiesConfig:
    reserve_timeout_seconds: int = 900
    request_timeout_seconds: float = 30.0
    messaging_channels: tuple[str, ...] = field(default=("whatsapp", "sms"))
