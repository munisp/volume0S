"""Studio terminal outcome metrics emitted from Temporal activities, not replayed workflows."""
from __future__ import annotations

import math
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest, start_http_server

REGISTRY = CollectorRegistry()
PACKS = Counter(
    "volumeos_studio_packs_total", "Studio packs reaching a recorded terminal outcome.",
    ["result"], registry=REGISTRY,
)
PACK_DURATION = Histogram(
    "volumeos_studio_pack_duration_seconds", "Studio intake-to-terminal-outcome duration.",
    ["result"], registry=REGISTRY,
    buckets=(60, 300, 900, 1800, 3600, 7200, 21600, 43200, 86400, 172800, 259200),
)


def record_outcome(result: str, duration_seconds: float) -> None:
    if result not in {"delivered", "rejected", "failed"}:
        raise ValueError("invalid Studio outcome")
    if not math.isfinite(duration_seconds) or duration_seconds < 0:
        raise ValueError("Studio duration must be finite and nonnegative")
    PACKS.labels(result=result).inc()
    PACK_DURATION.labels(result=result).observe(duration_seconds)


def render() -> bytes:
    return generate_latest(REGISTRY)


def start_server(port: int) -> Any:
    if not 1 <= port <= 65535:
        raise ValueError("invalid metrics port")
    return start_http_server(port, registry=REGISTRY)
