"""Operational Prometheus metrics for campaign activities.

Workflow code never writes these metrics directly: Temporal replay must not create
side effects. Absolute cycle gauges are safe to retry; counters/histograms are
operational measurements, not financial or billing records.
"""
from __future__ import annotations

import math
import re
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest, start_http_server

SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
CHANNELS = {"email", "sms", "whatsapp", "social"}
REGISTRY = CollectorRegistry()
APPROVALS = Counter(
    "volumeos_campaign_approvals_total", "Campaign approval decisions recorded by activities.",
    ["result"], registry=REGISTRY,
)
DISPATCH_DURATION = Histogram(
    "volumeos_campaign_dispatch_duration_seconds", "Provider dispatch latency.",
    ["channel", "result"], registry=REGISTRY,
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)
CYCLE_DISPATCHES = Gauge(
    "volumeos_campaign_cycle_dispatches", "Reconciled final dispatch attempts per campaign cycle.",
    ["tenant_id", "campaign_id", "cycle_id", "channel", "result"], registry=REGISTRY,
)


def observe_approval(result: str) -> None:
    if result not in {"approved", "rejected", "timeout"}:
        raise ValueError("invalid approval result")
    APPROVALS.labels(result=result).inc()


def observe_dispatch(channel: str, result: str, duration_seconds: float) -> None:
    if channel not in CHANNELS or result not in {"success", "failure"}:
        raise ValueError("invalid dispatch metric labels")
    if not math.isfinite(duration_seconds) or duration_seconds < 0:
        raise ValueError("dispatch duration must be finite and nonnegative")
    DISPATCH_DURATION.labels(channel=channel, result=result).observe(duration_seconds)


def record_cycle(tenant_id: str, campaign_id: str, cycle_id: str, channel: str, success: int, failure: int) -> None:
    if any(not SAFE_ID.fullmatch(value) for value in (tenant_id, campaign_id, cycle_id)):
        raise ValueError("invalid tenant, campaign, or cycle identifier")
    if channel not in CHANNELS:
        raise ValueError("invalid dispatch channel")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (success, failure)):
        raise ValueError("dispatch counts must be nonnegative integers")
    if success + failure == 0:
        raise ValueError("a reconciled cycle must contain attempts")
    labels = {"tenant_id": tenant_id, "campaign_id": campaign_id, "cycle_id": cycle_id, "channel": channel}
    CYCLE_DISPATCHES.labels(**labels, result="success").set(success)
    CYCLE_DISPATCHES.labels(**labels, result="failure").set(failure)


def render() -> bytes:
    return generate_latest(REGISTRY)


def start_server(port: int) -> Any:
    if not 1 <= port <= 65535:
        raise ValueError("invalid metrics port")
    return start_http_server(port, registry=REGISTRY)
