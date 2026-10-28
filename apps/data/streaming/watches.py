"""Flink-owned event-time SLA and cost watches with event-ID deduplication."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

EVENT_STATES = {"accepted", "sent", "delivered", "failed"}


@dataclass(frozen=True)
class WatchAlert:
    kind: str
    scope: str
    window_start: int
    value_ppm: int
    threshold_ppm: int


class KeyedWatch:
    """Small deterministic core invoked by a keyed PyFlink process function."""

    def __init__(self, window_seconds: int = 60):
        if window_seconds <= 0:
            raise ValueError("watch window must be positive")
        self.window_seconds = window_seconds
        self.seen: set[str] = set()
        self.alerts: set[tuple[str, str, int]] = set()

    def accept(self, event_id: str) -> bool:
        if event_id in self.seen:
            return False
        self.seen.add(event_id)
        return True

    def window(self, ts_unix: int) -> int:
        return ts_unix - ts_unix % self.window_seconds

    def emit_once(self, kind: str, scope: str, window: int, value_ppm: int, threshold_ppm: int) -> WatchAlert | None:
        key = (kind, scope, window)
        if key in self.alerts:
            return None
        self.alerts.add(key)
        return WatchAlert(kind, scope, window, value_ppm, threshold_ppm)


class SLAWatch(KeyedWatch):
    def __init__(self, minimum_delivery_ppm: int = 950_000, max_send_rate_ppm: int = 1_500_000,
                 minimum_volume: int = 20, window_seconds: int = 60):
        super().__init__(window_seconds)
        if not 0 <= minimum_delivery_ppm <= 1_000_000 or minimum_volume < 1:
            raise ValueError("invalid SLA watch configuration")
        self.minimum_delivery_ppm = minimum_delivery_ppm
        self.max_send_rate_ppm = max_send_rate_ppm
        self.minimum_volume = minimum_volume
        self.events: dict[tuple[str, int], list[str]] = {}

    def observe(self, event_id: str, tenant_id: str, campaign_id: str, status: str, ts_unix: int) -> list[WatchAlert]:
        if status not in EVENT_STATES:
            raise ValueError("unsupported delivery status")
        if not self.accept(event_id):
            return []
        window = self.window(ts_unix)
        scope = f"{tenant_id}/{campaign_id}"
        events = self.events.setdefault((scope, window), [])
        events.append(status)
        alerts: list[WatchAlert] = []
        if len(events) >= self.minimum_volume:
            delivered = events.count("delivered")
            ratio_ppm = delivered * 1_000_000 // len(events)
            if ratio_ppm < self.minimum_delivery_ppm:
                alert = self.emit_once("delivery-sla", scope, window, ratio_ppm, self.minimum_delivery_ppm)
                if alert:
                    alerts.append(alert)
        baseline = max(1, self.minimum_volume)
        send_rate_ppm = len(events) * 1_000_000 // baseline
        if send_rate_ppm > self.max_send_rate_ppm:
            alert = self.emit_once("send-rate-spike", scope, window, send_rate_ppm, self.max_send_rate_ppm)
            if alert:
                alerts.append(alert)
        return alerts


class CostWatch(KeyedWatch):
    def __init__(self, breach_ppm: int = 1_200_000, window_seconds: int = 300):
        super().__init__(window_seconds)
        self.breach_ppm = breach_ppm
        self.actual_minor: dict[tuple[str, int], int] = {}

    def observe(self, event_id: str, tenant_id: str, amount_minor: int, planned_minor: int,
                ts_unix: int) -> WatchAlert | None:
        if amount_minor < 0 or planned_minor <= 0:
            raise ValueError("cost amounts must be non-negative and plan must be positive")
        if not self.accept(event_id):
            return None
        window = self.window(ts_unix)
        key = (tenant_id, window)
        self.actual_minor[key] = self.actual_minor.get(key, 0) + amount_minor
        burn_ppm = self.actual_minor[key] * 1_000_000 // planned_minor
        if burn_ppm >= self.breach_ppm:
            return self.emit_once("cost-breach", tenant_id, window, burn_ppm, self.breach_ppm)
        return None
