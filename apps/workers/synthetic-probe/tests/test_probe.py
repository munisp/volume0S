import logging

import httpx
import pytest

import main as probe


def settings():
    return probe.Settings(
        environment="stage",
        tenant_id="synthetic-tenant",
        oidc_token_url="https://identity.example.test/token",
        oidc_client_id="synthetic-client",
        oidc_client_secret="oidc-secret-value",
        core_api_url="http://svc-tenancy.core.svc.cluster.local",
        send_url="https://sandbox.example.test/synthetic/send",
        service_token="service-token-value-at-least-32-characters",
    )


def environment(**overrides):
    values = {
        "SYNTHETIC_ENVIRONMENT": "stage",
        "SYNTHETIC_TENANT_ID": "synthetic-tenant",
        "OIDC_TOKEN_URL": "https://identity.example.test/token",
        "OIDC_CLIENT_ID": "synthetic-client",
        "OIDC_CLIENT_SECRET": "oidc-secret-value",
        "CORE_API_URL": "http://svc-tenancy.core.svc.cluster.local",
        "SYNTHETIC_SEND_URL": "https://sandbox.example.test/synthetic/send",
        "VOLUMEOS_SERVICE_TOKEN": "service-token-value-at-least-32-characters",
    }
    values.update(overrides)
    return values


def sample(name, journey):
    for metric in probe.REGISTRY.collect():
        for point in metric.samples:
            if point.name == name and point.labels == {"journey": journey}:
                return point.value
    return None


def test_settings_reject_production_missing_and_credential_urls(monkeypatch):
    monkeypatch.setattr(probe.os, "environ", environment(SYNTHETIC_ENVIRONMENT="prod"))
    with pytest.raises(ValueError, match="dev/stage"):
        probe.load_settings()
    monkeypatch.setattr(probe.os, "environ", environment(OIDC_CLIENT_SECRET=""))
    with pytest.raises(ValueError, match="credentials"):
        probe.load_settings()
    monkeypatch.setattr(
        probe.os, "environ",
        environment(SYNTHETIC_SEND_URL="https://user:password@sandbox.example.test/send"),
    )
    with pytest.raises(ValueError, match="embedded credentials"):
        probe.load_settings()


async def test_login_uses_oidc_token_and_authorized_tenant_request():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/token":
            assert request.content == b"grant_type=client_credentials&client_id=synthetic-client&client_secret=oidc-secret-value"
            return httpx.Response(200, json={"access_token": "access-token-value"})
        assert request.url.path == "/v1/tenants/current"
        assert request.headers["Authorization"] == "Bearer access-token-value"
        assert request.headers["X-VolumeOS-Tenant"] == "synthetic-tenant"
        return httpx.Response(200, json={"id": "synthetic-tenant"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await probe.login_journey(client, settings())
    assert len(requests) == 2


@pytest.mark.parametrize("body", [
    {"status": "accepted", "synthetic": True, "receipt_id": "receipt-1"},
    {"status": "queued", "synthetic": True, "receipt_id": "receipt-1"},
    {"status": "ok"},
    {"status": "completed", "synthetic": False, "receipt_id": "receipt-1"},
    {"status": "completed", "synthetic": True, "receipt_id": None},
])
async def test_send_rejects_health_accepted_queued_and_invalid_receipts(body):
    async def reject():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=body)
        )) as client:
            await probe.send_journey(client, settings())

    with pytest.raises(ValueError, match="completed synthetic provider receipt"):
        await reject()


async def test_send_requires_completed_receipt_and_never_puts_secrets_in_metrics(caplog):
    seen = {}

    def handler(request):
        seen["authorization"] = request.headers["Authorization"]
        seen["synthetic"] = request.headers["X-VolumeOS-Synthetic"]
        seen["idempotency"] = request.headers["Idempotency-Key"]
        return httpx.Response(200, json={
            "status": "completed", "synthetic": True, "receipt_id": "provider-receipt-1",
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await probe.measured("send", lambda: probe.send_journey(client, settings()))
        assert sample("probe_success", "send") == 1
        assert sample("probe_duration_seconds", "send") >= 0

        async def fail():
            raise RuntimeError("oidc-secret-value service-token-value-at-least-32-characters")

        with caplog.at_level(logging.WARNING, logger=probe.LOGGER.name):
            assert not await probe.measured("login", fail)
    assert seen["authorization"] == "Bearer service-token-value-at-least-32-characters"
    assert seen["synthetic"] == "send"
    assert seen["idempotency"].startswith("synthetic-send:")
    assert sample("probe_success", "login") == 0
    rendered = probe.REGISTRY
    exposition = b"".join(metric.name.encode() for metric in rendered.collect())
    assert b"oidc-secret-value" not in exposition
    assert b"service-token-value" not in exposition
    assert "oidc-secret-value" not in caplog.text
    assert "service-token-value" not in caplog.text


async def test_journey_loop_uses_five_minute_cadence(monkeypatch):
    assert probe.INTERVAL_SECONDS == 300
    sleeps = []

    async def once(_journey, operation):
        await operation()
        return True

    async def sleep(duration):
        sleeps.append(duration)
        raise asyncio.CancelledError

    import asyncio

    async def operation():
        return None

    monkeypatch.setattr(probe, "measured", once)
    monkeypatch.setattr(probe.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await probe.journey_loop("login", operation)
    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(300, abs=0.001)
