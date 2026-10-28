"""M1 provider adapters with mandatory policy, replay, and usage boundaries.

The dispatcher is deliberately transport-agnostic. Production wiring must provide
persistent idempotency and a tenant-scoped policy service; in-memory fakes belong
only in tests. No provider call is made when any guard is unavailable.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx
from temporalio.exceptions import ApplicationError

import metrics

TENANT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class DispatchRequest:
    tenant_id: str
    campaign_id: str
    channel: str
    approval_id: str
    content: str
    subject: str = "VolumeOS campaign"
    recipient: str | None = None
    scheduled_at: datetime | None = None
    postiz_integration_id: str | None = None
    # Customer rating is explicit and remains separate from provider cost.
    unit_charge_minor: int = 0
    offer: str = "01"


@dataclass(frozen=True)
class ProviderResult:
    provider_id: str
    cost_minor: int = 0
    currency: str = "NGN"


@dataclass(frozen=True)
class UsageEvent:
    event_id: str
    tenant_id: str
    campaign_id: str
    channel: str
    count: int
    cost_minor: int
    currency: str
    status: str
    charge_minor: int = 0
    provider: str = ""
    offer: str = "01"


class DispatchPolicy(Protocol):
    async def authorize(self, value: DispatchRequest) -> None: ...


class IdempotencyStore(Protocol):
    async def get(self, key: str) -> ProviderResult | None: ...
    async def put(self, key: str, result: ProviderResult) -> None: ...


class UsageSink(Protocol):
    async def emit(self, event: UsageEvent) -> None: ...


class Provider(Protocol):
    async def send(self, value: DispatchRequest, idempotency_key: str) -> ProviderResult: ...


def validate_dispatch(value: DispatchRequest) -> None:
    if not TENANT_RE.fullmatch(value.tenant_id):
        raise ApplicationError("invalid tenant_id", non_retryable=True)
    if not ID_RE.fullmatch(value.campaign_id) or not ID_RE.fullmatch(value.approval_id):
        raise ApplicationError("valid campaign_id and approval_id are required", non_retryable=True)
    if value.channel not in {"email", "social"}:
        raise ApplicationError("unsupported M1 provider channel", non_retryable=True)
    if value.unit_charge_minor < 0:
        raise ApplicationError("unit charge cannot be negative", non_retryable=True)
    if value.offer not in {f"{number:02}" for number in range(1, 9)}:
        raise ApplicationError("campaign offer must be one of 01 through 08", non_retryable=True)
    if not value.content.strip():
        raise ApplicationError("approved content is required", non_retryable=True)
    if value.channel == "email" and (value.recipient is None or not value.recipient.strip()):
        raise ApplicationError("email recipient is required for suppression enforcement", non_retryable=True)
    if value.scheduled_at and value.scheduled_at.tzinfo is None:
        raise ApplicationError("scheduled_at must include a timezone", non_retryable=True)


class GuardedDispatcher:
    def __init__(self, providers: dict[str, Provider], policy: DispatchPolicy, store: IdempotencyStore, usage: UsageSink):
        self._providers = providers
        self._policy = policy
        self._store = store
        self._usage = usage

    async def dispatch(self, value: DispatchRequest) -> ProviderResult:
        validate_dispatch(value)
        key = f"{value.tenant_id}:{value.campaign_id}:{value.channel}"
        existing = await self._store.get(key)
        if existing is not None:
            return existing
        await self._policy.authorize(value)
        try:
            provider = self._providers[value.channel]
        except KeyError as exc:
            raise ApplicationError("provider is not configured", non_retryable=True) from exc
        started = time.perf_counter()
        try:
            result = await provider.send(value, key)
            if not result.provider_id.strip() or result.cost_minor < 0:
                raise ApplicationError("provider returned an invalid result")
        except BaseException:
            metrics.observe_dispatch(value.channel, "failure", time.perf_counter() - started)
            raise
        metrics.observe_dispatch(value.channel, "success", time.perf_counter() - started)
        await self._store.put(key, result)
        await self._usage.emit(UsageEvent(
            event_id=f"dispatch:{key}", tenant_id=value.tenant_id,
            campaign_id=value.campaign_id, channel=value.channel, count=1,
            cost_minor=result.cost_minor, currency=result.currency, status="accepted",
            charge_minor=value.unit_charge_minor, provider=value.channel, offer=value.offer,
        ))
        return result


class HTTPDispatchPolicy:
    """Fail-closed adapter for tenancy kill-switch and suppression decisions."""

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None):
        if not base_url.strip():
            raise ValueError("policy base_url is required")
        self._base_url = base_url.rstrip("/")
        self._client = client

    async def authorize(self, value: DispatchRequest) -> None:
        payload = {"channel": value.channel, "recipient": value.recipient}
        headers = {"X-VolumeOS-Tenant": value.tenant_id, "Idempotency-Key": f"authorize:{value.campaign_id}:{value.channel}"}
        token = os.environ.get("VOLUMEOS_SERVICE_TOKEN", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if self._client is None:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self._base_url + "/v1/dispatch-authorizations", json=payload, headers=headers)
        else:
            response = await self._client.post(self._base_url + "/v1/dispatch-authorizations", json=payload, headers=headers)
        response.raise_for_status()
        decision = response.json()
        if decision.get("allowed") is not True:
            raise ApplicationError("dispatch denied by kill switch or suppression registry", non_retryable=True)


class HTTPIdempotencyStore:
    """Persistent tenant-scoped result store backed by svc-tenancy/PostgreSQL."""

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None):
        if not base_url.strip():
            raise ValueError("idempotency base_url is required")
        self._base_url = base_url.rstrip("/") + "/v1/dispatch-results"
        self._client = client

    def _headers(self, key: str) -> dict[str, str]:
        tenant_id, separator, _ = key.partition(":")
        if not separator or not TENANT_RE.fullmatch(tenant_id):
            raise ApplicationError("idempotency key must be tenant scoped", non_retryable=True)
        headers = {"X-VolumeOS-Tenant": tenant_id, "Idempotency-Key": key}
        token = os.environ.get("VOLUMEOS_SERVICE_TOKEN", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _request(self, method: str, **kwargs: Any) -> httpx.Response:
        if self._client is None:
            async with httpx.AsyncClient(timeout=10.0) as client:
                return await client.request(method, self._base_url, **kwargs)
        return await self._client.request(method, self._base_url, **kwargs)

    async def get(self, key: str) -> ProviderResult | None:
        response = await self._request("GET", headers=self._headers(key), params={"key": key})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        body = response.json()
        return ProviderResult(
            provider_id=str(body["provider_id"]), cost_minor=int(body["cost_minor"]),
            currency=str(body["currency"]),
        )

    async def put(self, key: str, result: ProviderResult) -> None:
        response = await self._request("PUT", headers=self._headers(key), json={
            "key": key, "provider_id": result.provider_id,
            "cost_minor": result.cost_minor, "currency": result.currency,
        })
        response.raise_for_status()


class DaprUsageSink:
    def __init__(self, dapr_url: str, pubsub: str = "kafka-pubsub", client: httpx.AsyncClient | None = None):
        if not dapr_url.strip():
            raise ValueError("Dapr URL is required")
        self._url = dapr_url.rstrip("/") + f"/v1.0/publish/{pubsub}/usage.events"
        self._client = client

    async def emit(self, event: UsageEvent) -> None:
        from dataclasses import asdict
        headers = {"X-VolumeOS-Tenant": event.tenant_id, "Idempotency-Key": event.event_id}
        if self._client is None:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self._url, json=asdict(event), headers=headers)
        else:
            response = await self._client.post(self._url, json=asdict(event), headers=headers)
        response.raise_for_status()


class ListMonkProvider:
    def __init__(self, base_url: str, username: str, password: str, tenant_lists: dict[str, int], client: httpx.AsyncClient | None = None):
        if not all((base_url.strip(), username, password)):
            raise ValueError("ListMonk URL and credentials are required")
        self._base_url = base_url.rstrip("/")
        self._auth = (username, password)
        self._tenant_lists = tenant_lists
        self._client = client

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        kwargs.update(auth=self._auth, timeout=30.0)
        if self._client is None:
            async with httpx.AsyncClient() as client:
                return await client.request(method, self._base_url + path, **kwargs)
        return await self._client.request(method, self._base_url + path, **kwargs)

    async def send(self, value: DispatchRequest, idempotency_key: str) -> ProviderResult:
        try:
            list_id = self._tenant_lists[value.tenant_id]
        except KeyError as exc:
            raise ApplicationError("tenant has no ListMonk list binding", non_retryable=True) from exc
        headers = {"Idempotency-Key": idempotency_key, "X-VolumeOS-Tenant": value.tenant_id}
        response = await self._request("POST", "/api/campaigns", headers=headers, json={
            "name": f"volumeos-{value.campaign_id}", "subject": value.subject,
            "lists": [list_id], "from_email": "campaigns@volumeos.local",
            "messenger": "email", "type": "regular", "content_type": "plain",
            "body": value.content,
        })
        response.raise_for_status()
        campaign_id = str(response.json().get("data", {}).get("id", ""))
        if not campaign_id:
            raise ApplicationError("ListMonk response is missing campaign id")
        status = await self._request("PUT", f"/api/campaigns/{campaign_id}/status", headers=headers, json={"status": "running"})
        status.raise_for_status()
        return ProviderResult(provider_id=campaign_id)


class PostizProvider:
    """Postiz scheduler with production tenant-scoped Dapr credential lookup.

    A static api_key exists for isolated adapter tests only. Production construction
    must provide dapr_url and secret_store instead of a shared provider credential.
    """

    def __init__(
        self, base_url: str, api_key: str = "", client: httpx.AsyncClient | None = None,
        dapr_url: str = "", secret_store: str = "",
    ):
        if not base_url.strip():
            raise ValueError("Postiz URL is required")
        if api_key and dapr_url:
            raise ValueError("use either a test API key or Dapr tenant credentials, not both")
        if not api_key and (not dapr_url.strip() or not secret_store.strip()):
            raise ValueError("Dapr URL and secret store are required for tenant credentials")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._dapr_url = dapr_url.rstrip("/")
        self._secret_store = secret_store
        self._client = client

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        kwargs.setdefault("timeout", 30.0)
        if self._client is None:
            async with httpx.AsyncClient() as client:
                return await client.request(method, url, **kwargs)
        return await self._client.request(method, url, **kwargs)

    async def _tenant_api_key(self, tenant_id: str) -> str:
        if self._api_key:
            return self._api_key
        secret_name = f"postiz-{tenant_id}-api-key"
        headers = {}
        if token := os.environ.get("DAPR_API_TOKEN"):
            headers["dapr-api-token"] = token
        response = await self._request(
            "GET", f"{self._dapr_url}/v1.0/secrets/{self._secret_store}/{secret_name}", headers=headers,
        )
        response.raise_for_status()
        secrets = response.json()
        if not isinstance(secrets, dict) or not isinstance(secrets.get(secret_name), str) or not secrets[secret_name]:
            raise ApplicationError("tenant Postiz credential is missing", non_retryable=True)
        return secrets[secret_name]

    async def send(self, value: DispatchRequest, idempotency_key: str) -> ProviderResult:
        if not value.postiz_integration_id or value.scheduled_at is None:
            raise ApplicationError("Postiz integration and schedule are required", non_retryable=True)
        when = value.scheduled_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        headers = {
            "Authorization": await self._tenant_api_key(value.tenant_id), "Idempotency-Key": idempotency_key,
            "X-VolumeOS-Tenant": value.tenant_id,
        }
        payload = {
            "type": "schedule", "date": when, "shortLink": False,
            "posts": [{"integration": {"id": value.postiz_integration_id}, "value": [{"content": value.content, "image": []}]}],
        }
        if self._client is None:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(self._base_url + "/api/public/v1/posts", json=payload, headers=headers)
        else:
            response = await self._client.post(self._base_url + "/api/public/v1/posts", json=payload, headers=headers)
        response.raise_for_status()
        body = response.json()
        provider_id = ""
        if isinstance(body, dict):
            provider_id = str(body.get("id") or body.get("data", {}).get("id") or "")
        if not provider_id:
            raise ApplicationError("Postiz response is missing post id")
        return ProviderResult(provider_id=provider_id)
