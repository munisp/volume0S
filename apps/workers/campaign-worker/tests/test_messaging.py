import json
from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest
from temporalio.exceptions import ApplicationError

from messaging import (
    DaprUsageSink,
    GuardedDispatcher,
    HTTPDispatchPolicy,
    HTTPIdempotencyStore,
    ListMonkProvider,
    PostizProvider,
    ProviderResult,
    DispatchRequest,
    UsageEvent,
    validate_dispatch,
)


class FakePolicy:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.calls = 0

    async def authorize(self, value):
        self.calls += 1
        if not self.allowed:
            raise ApplicationError("denied", non_retryable=True)


class FakeStore:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def put(self, key, result):
        self.values[key] = result


class FakeUsage:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


class FakeProvider:
    def __init__(self):
        self.calls = 0

    async def send(self, value, key):
        self.calls += 1
        return ProviderResult("provider-1", 760)


def request(channel="email"):
    return DispatchRequest(
        tenant_id="tenant-1", campaign_id="campaign-1", channel=channel,
        approval_id="approval-1", content="Approved copy", recipient="owner@example.com",
        postiz_integration_id="channel-1" if channel == "social" else None,
        scheduled_at=datetime(2026, 11, 1, 12, tzinfo=timezone.utc) if channel == "social" else None,
    )


async def test_guarded_dispatch_is_idempotent_and_emits_usage():
    policy, store, usage, provider = FakePolicy(), FakeStore(), FakeUsage(), FakeProvider()
    dispatcher = GuardedDispatcher({"email": provider}, policy, store, usage)
    first = await dispatcher.dispatch(request())
    second = await dispatcher.dispatch(request())
    assert first == second == ProviderResult("provider-1", 760)
    assert provider.calls == 1
    assert policy.calls == 1
    assert len(usage.events) == 1
    event = usage.events[0]
    assert (event.tenant_id, event.channel, event.cost_minor, event.status) == ("tenant-1", "email", 760, "accepted")


async def test_denied_dispatch_never_calls_provider():
    provider = FakeProvider()
    dispatcher = GuardedDispatcher({"email": provider}, FakePolicy(False), FakeStore(), FakeUsage())
    with pytest.raises(ApplicationError):
        await dispatcher.dispatch(request())
    assert provider.calls == 0


@pytest.mark.parametrize("change", [
    {"tenant_id": "bad tenant"}, {"campaign_id": ""}, {"approval_id": ""},
    {"content": " "}, {"recipient": None}, {"channel": "sms"}, {"scheduled_at": datetime(2026, 11, 1)},
])
def test_dispatch_validation_rejects_unsafe_input(change):
    values = request("social") if "scheduled_at" in change else request()
    values = replace(values, **change)
    with pytest.raises(ApplicationError):
        validate_dispatch(values)


async def test_listmonk_uses_only_bound_tenant_list_and_starts_approved_campaign():
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.path == "/api/campaigns":
            return httpx.Response(200, json={"data": {"id": 42}})
        assert req.url.path == "/api/campaigns/42/status"
        return httpx.Response(200, json={"data": {}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://listmonk")
    provider = ListMonkProvider("http://listmonk", "admin", "secret", {"tenant-1": 7}, client)
    result = await provider.send(request(), "tenant-1:campaign-1:email")
    assert result.provider_id == "42"
    assert len(seen) == 2
    assert json.loads(seen[0].content)["lists"] == [7]
    assert seen[0].headers["X-VolumeOS-Tenant"] == "tenant-1"
    assert json.loads(seen[1].content) == {"status": "running"}
    await client.aclose()


async def test_listmonk_rejects_cross_tenant_list_lookup():
    provider = ListMonkProvider("http://listmonk", "admin", "secret", {"another-tenant": 7})
    with pytest.raises(ApplicationError):
        await provider.send(request(), "key")


async def test_postiz_sends_schedule_with_approval_scoped_request():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"id": "post-1"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://postiz")
    provider = PostizProvider("http://postiz", "tenant-api-key", client)
    result = await provider.send(request("social"), "tenant-1:campaign-1:social")
    assert result.provider_id == "post-1"
    payload = json.loads(seen[0].content)
    assert payload["type"] == "schedule"
    assert payload["date"] == "2026-11-01T12:00:00Z"
    assert payload["posts"][0]["integration"]["id"] == "channel-1"
    assert seen[0].headers["Authorization"] == "tenant-api-key"
    await client.aclose()


async def test_postiz_resolves_tenant_credential_from_dapr(monkeypatch):
    monkeypatch.setenv("DAPR_API_TOKEN", "dapr-token")
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.path == "/v1.0/secrets/tenant-secrets/postiz-tenant-1-api-key":
            assert req.headers["dapr-api-token"] == "dapr-token"
            return httpx.Response(200, json={"postiz-tenant-1-api-key": "tenant-1-key"})
        if req.url.path == "/api/public/v1/posts":
            return httpx.Response(200, json={"id": "post-1"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = PostizProvider(
        "http://postiz", client=client, dapr_url="http://dapr", secret_store="tenant-secrets",
    )
    result = await provider.send(request("social"), "tenant-1:campaign-1:social")
    assert result.provider_id == "post-1"
    assert seen[-1].headers["Authorization"] == "tenant-1-key"
    await client.aclose()


async def test_http_policy_requires_explicit_allow():
    def handler(req):
        return httpx.Response(200, json={"allowed": False, "reason": "suppressed"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    policy = HTTPDispatchPolicy("http://policy", client)
    with pytest.raises(ApplicationError):
        await policy.authorize(request())
    await client.aclose()


async def test_dapr_usage_sink_publishes_tenant_scoped_event():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = DaprUsageSink("http://dapr", client=client)
    await sink.emit(UsageEvent("event-1", "tenant-1", "campaign-1", "email", 1, 0, "NGN", "accepted"))
    assert seen[0].url.path == "/v1.0/publish/kafka-pubsub/usage.events"
    assert seen[0].headers["X-VolumeOS-Tenant"] == "tenant-1"
    assert json.loads(seen[0].content)["cost_minor"] == 0
    await client.aclose()


async def test_http_idempotency_store_uses_scoped_persistent_results(monkeypatch):
    monkeypatch.setenv("VOLUMEOS_SERVICE_TOKEN", "service-token")
    stored = {}
    seen = []

    def handler(req):
        seen.append(req)
        key = req.url.params["key"] if req.method == "GET" else json.loads(req.content)["key"]
        if req.method == "GET" and key not in stored:
            return httpx.Response(404, json={"error": "missing"})
        if req.method == "PUT":
            stored[key] = json.loads(req.content)
        return httpx.Response(200, json=stored[key])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = HTTPIdempotencyStore("http://tenancy", client)
    key = "tenant-1:campaign-1:email"
    assert await store.get(key) is None
    await store.put(key, ProviderResult("provider-1", 760))
    assert await store.get(key) == ProviderResult("provider-1", 760)
    assert all(req.headers["X-VolumeOS-Tenant"] == "tenant-1" for req in seen)
    assert all(req.headers["Authorization"] == "Bearer service-token" for req in seen)
    await client.aclose()


async def test_http_policy_sends_internal_service_credentials(monkeypatch):
    monkeypatch.setenv("VOLUMEOS_SERVICE_TOKEN", "service-token")

    def handler(req):
        assert req.headers["Authorization"] == "Bearer service-token"
        assert req.headers["X-VolumeOS-Tenant"] == "tenant-1"
        return httpx.Response(200, json={"allowed": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await HTTPDispatchPolicy("http://tenancy", client).authorize(request())
    await client.aclose()
