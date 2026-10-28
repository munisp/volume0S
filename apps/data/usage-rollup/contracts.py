"""Validated Kafka rollup contracts shared by the usage billing dispatcher."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
CHANNELS = {"campaign", "sms", "whatsapp", "email", "social", "ai", "gpu"}


class RollupError(ValueError):
    pass


def integer(value: Any, name: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise RollupError(f"{name} must be an integer or integer string")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise RollupError(f"{name} must be an integer") from error
    if str(value).strip() != str(result):
        raise RollupError(f"{name} must be a canonical integer")
    if result < 0 or (positive and result == 0):
        raise RollupError(f"{name} is outside the allowed range")
    return result


@dataclass(frozen=True)
class ClosedDayRollup:
    tenant_id: str
    provider: str
    channel: str
    campaign_id: str
    offer: str
    day: str
    event_count: int
    amount_minor: int
    provider_cost_minor: int
    currency: str

    @classmethod
    def parse(cls, value: Any) -> "ClosedDayRollup":
        if not isinstance(value, dict):
            raise RollupError("rollup must be a JSON object")
        required = {
            "tenant_id", "provider", "channel", "campaign_id", "offer", "day",
            "event_count", "amount_minor", "provider_cost_minor", "currency",
        }
        if set(value) != required:
            raise RollupError("rollup fields do not match the versioned contract")
        result = cls(
            tenant_id=str(value["tenant_id"]),
            provider=str(value["provider"]),
            channel=str(value["channel"]),
            campaign_id=str(value["campaign_id"]),
            offer=str(value["offer"]),
            day=str(value["day"]),
            event_count=integer(value["event_count"], "event_count", positive=True),
            amount_minor=integer(value["amount_minor"], "amount_minor", positive=True),
            provider_cost_minor=integer(value["provider_cost_minor"], "provider_cost_minor"),
            currency=str(value["currency"]),
        )
        result.validate()
        return result

    def validate(self) -> None:
        if not all(SAFE_ID.fullmatch(value) for value in (self.tenant_id, self.provider, self.campaign_id)):
            raise RollupError("valid tenant, provider, and campaign identifiers are required")
        if self.channel not in CHANNELS or self.currency not in {"NGN", "USD"}:
            raise RollupError("unsupported rollup channel or currency")
        if self.offer not in {f"{number:02}" for number in range(1, 9)}:
            raise RollupError("rollup offer must be one of 01 through 08")
        try:
            date.fromisoformat(self.day)
        except ValueError as error:
            raise RollupError("rollup day must be an ISO date") from error

    @property
    def week(self) -> str:
        iso = date.fromisoformat(self.day).isocalendar()
        return f"{iso.year}-W{iso.week:02d}"

    @property
    def pending_key(self) -> str:
        return f"{self.workflow_id}:{self.day}"

    @property
    def workflow_id(self) -> str:
        return (
            f"usage:{self.tenant_id}:{self.provider}:{self.channel}:"
            f"{self.campaign_id}:{self.offer}:{self.week}"
        )

    def workflow_input(self, close_delay: timedelta) -> dict[str, Any]:
        seconds = int(close_delay.total_seconds())
        if not 3600 <= seconds <= 604_800:
            raise RollupError("close delay must be between 1 and 168 hours")
        value = asdict(self)
        value["close_delay_seconds"] = seconds
        return value

    def close_time(self, delay: timedelta) -> datetime:
        if delay < timedelta(0):
            raise RollupError("close delay cannot be negative")
        following_day = date.fromisoformat(self.day) + timedelta(days=1)
        return datetime.combine(following_day, time.min, tzinfo=timezone.utc) + delay

    def week_close_time(self, delay: timedelta) -> datetime:
        if delay < timedelta(0):
            raise RollupError("close delay cannot be negative")
        current = date.fromisoformat(self.day)
        following_week = current + timedelta(days=7 - current.weekday())
        return datetime.combine(following_week, time.min, tzinfo=timezone.utc) + delay

    def is_closed(self, now: datetime, delay: timedelta) -> bool:
        if now.tzinfo is None:
            raise RollupError("current time must include a timezone")
        return now.astimezone(timezone.utc) >= self.close_time(delay)
