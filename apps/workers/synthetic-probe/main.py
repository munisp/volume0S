"""Five-minute M1 login/send synthetic journeys with real endpoint validation.

The send URL must be a dedicated, pre-approved staging sandbox journey. It must
return a completed synthetic receipt; health checks and accepted/queued responses
are deliberately rejected. Payment probing is M2 scope.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import math
import os
import time
from urllib.parse import urlsplit
import uuid

import httpx
from prometheus_client import CollectorRegistry, Gauge, start_http_server

LOGGER = logging.getLogger("volumeos-synthetic")
REGISTRY = CollectorRegistry()
SUCCESS = Gauge("probe_success", "Whether the latest synthetic journey succeeded.", ["journey"], registry=REGISTRY)
DURATION = Gauge("probe_duration_seconds", "Latest synthetic journey duration.", ["journey"], registry=REGISTRY)
INTERVAL_SECONDS = 300.0


@dataclass(frozen=True)
class Settings:
    environment: str
    tenant_id: str
    oidc_token_url: str
    oidc_client_id: str
    oidc_client_secret: str
    core_api_url: str
    send_url: str
    service_token: str


def safe_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("journey URLs must be HTTP(S) URLs without embedded credentials")
    return value.rstrip("/")


def load_settings() -> Settings:
    settings = Settings(
        environment=os.environ.get("SYNTHETIC_ENVIRONMENT", ""),
        tenant_id=os.environ.get("SYNTHETIC_TENANT_ID", ""),
        oidc_token_url=safe_url(os.environ.get("OIDC_TOKEN_URL", "")),
        oidc_client_id=os.environ.get("OIDC_CLIENT_ID", ""),
        oidc_client_secret=os.environ.get("OIDC_CLIENT_SECRET", ""),
        core_api_url=safe_url(os.environ.get("CORE_API_URL", "")),
        send_url=safe_url(os.environ.get("SYNTHETIC_SEND_URL", "")),
        service_token=os.environ.get("VOLUMEOS_SERVICE_TOKEN", ""),
    )
    if settings.environment not in {"dev", "stage"}:
        raise ValueError("synthetic journeys are restricted to dev/stage")
    if not settings.tenant_id.strip() or not settings.oidc_client_id or not settings.oidc_client_secret:
        raise ValueError("synthetic tenant and OIDC client credentials are required")
    if len(settings.service_token) < 32:
        raise ValueError("a valid internal service token is required")
    return settings


async def login_journey(client: httpx.AsyncClient, settings: Settings) -> None:
    response = await client.post(settings.oidc_token_url, data={
        "grant_type": "client_credentials", "client_id": settings.oidc_client_id,
        "client_secret": settings.oidc_client_secret,
    })
    response.raise_for_status()
    token = response.json().get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError("OIDC response did not contain an access token")
    core = await client.get(
        settings.core_api_url + "/v1/tenants/current",
        headers={"Authorization": f"Bearer {token}", "X-VolumeOS-Tenant": settings.tenant_id},
    )
    core.raise_for_status()


async def send_journey(client: httpx.AsyncClient, settings: Settings) -> None:
    response = await client.post(settings.send_url, headers={
        "Authorization": f"Bearer {settings.service_token}",
        "X-VolumeOS-Tenant": settings.tenant_id,
        "X-VolumeOS-Synthetic": "send",
        "Idempotency-Key": f"synthetic-send:{uuid.uuid4()}",
    }, json={"synthetic": True, "journey": "send"})
    response.raise_for_status()
    body = response.json()
    receipt_id = body.get("receipt_id") if isinstance(body, dict) else None
    if (
        not isinstance(body, dict)
        or body.get("status") != "completed"
        or body.get("synthetic") is not True
        or not isinstance(receipt_id, str)
        or not receipt_id.strip()
    ):
        raise ValueError("send journey did not return a completed synthetic provider receipt")


async def measure(journey: str, operation) -> bool:
    try:
        await operation()
    except Exception as exc:  # Network/provider failures are probe results, not process crashes.
        SUCCESS.labels(journey=journey).set(0)
        LOGGER.warning("%s journey failed: %s", journey, type(exc).__name__)
        return False
    SUCCESS.labels(journey=journey).set(1)
    return True


async def measured(journey: str, operation) -> bool:
    started = time.perf_counter()
    try:
        return await measure(journey, operation)
    finally:
        duration = time.perf_counter() - started
        if math.isfinite(duration) and duration >= 0:
            DURATION.labels(journey=journey).set(duration)


async def journey_loop(journey: str, operation) -> None:
    while True:
        started = time.monotonic()
        await measured(journey, operation)
        await asyncio.sleep(max(0.0, INTERVAL_SECONDS - (time.monotonic() - started)))


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = load_settings()
    start_http_server(int(os.environ.get("METRICS_PORT", "9092")), registry=REGISTRY)
    timeout = httpx.Timeout(30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        await asyncio.gather(
            journey_loop("login", lambda: login_journey(client, settings)),
            journey_loop("send", lambda: send_journey(client, settings)),
        )


if __name__ == "__main__":
    asyncio.run(main())
