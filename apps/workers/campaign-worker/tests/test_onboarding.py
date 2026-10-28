import pytest
from temporalio.exceptions import ApplicationError

import onboarding
from onboarding import OnboardingInput, saga_compensations, validate_onboarding


def value(**changes):
    base = dict(tenant_id="tenant-1", name="Demo Gym", slug="demo-gym", corridor="lagos-yaba", offers=["01", "02"], postiz_integration_id="channel-1")
    base.update(changes)
    return OnboardingInput(**base)


def test_valid_m1_onboarding():
    validate_onboarding(value())


@pytest.mark.parametrize("changes", [
    {"tenant_id": "bad tenant"}, {"corridor": ""}, {"slug": "Bad Slug"},
    {"name": "x"}, {"offers": []}, {"offers": ["03"]},
    {"postiz_integration_id": None}, {"postiz_integration_id": "bad integration"},
])
def test_invalid_onboarding_fails_before_side_effects(changes):
    with pytest.raises(ApplicationError):
        validate_onboarding(value(**changes))


def test_compensation_is_reverse_and_excludes_read_only_steps():
    completed = ["identity", "tenancy", "listmonk", "postiz", "active", "welcome"]
    assert saga_compensations(completed) == ["undo:listmonk", "undo:tenancy", "undo:identity"]
    assert saga_compensations(["identity", "tenancy"]) == ["undo:tenancy", "undo:identity"]
    assert saga_compensations([]) == []


def test_unknown_saga_step_is_rejected():
    with pytest.raises(ValueError):
        saga_compensations(["identity", "ledger"])


async def test_postiz_validation_passes_existing_customer_integration(monkeypatch):
    captured = {}

    async def fake_provision(value, step, service_env, path, payload, result_key):
        captured.update(payload)
        return payload["integration_id"]

    monkeypatch.setattr(onboarding, "provision", fake_provision)
    result = await onboarding.initialize_postiz(value())
    assert result == "channel-1"
    assert captured == {
        "tenant_id": "tenant-1", "integration_id": "channel-1", "offers": ["01", "02"],
    }


@pytest.mark.parametrize("created,expected", [(True, "volumeos-tenant-1"), (False, "")])
async def test_identity_compensation_tracks_client_ownership(monkeypatch, created, expected):
    calls = []

    async def fake_request(service_env, method, path, tenant_id, key, payload=None):
        calls.append((service_env, method, path))
        return {"client_id": "volumeos-tenant-1", "created": created}

    monkeypatch.setattr(onboarding, "request", fake_request)
    monkeypatch.setattr(onboarding.activity, "heartbeat", lambda: None)
    resource = await onboarding.provision_identity(value())
    assert resource == expected
    await onboarding.compensate_onboarding(value(), "undo:identity", resource)
    if created:
        assert calls[-1] == ("IDENTITY_ADMIN_URL", "DELETE", "/v1/tenants/tenant-1")
    else:
        assert len(calls) == 1


async def test_billing_accounts_are_provisioned_before_activation(monkeypatch):
    captured = {}

    async def fake_request(service_env, method, path, tenant_id, key, payload=None):
        captured.update(service=service_env, method=method, path=path, tenant=tenant_id, payload=payload)
        return {"accounts_provisioned": 38}

    monkeypatch.setattr(onboarding, "request", fake_request)
    monkeypatch.setattr(onboarding.activity, "heartbeat", lambda: None)
    assert await onboarding.provision_billing_accounts(value()) == 38
    assert captured == {
        "service": "BILLING_SERVICE_URL", "method": "POST",
        "path": "/v1/tenants/tenant-1/accounts", "tenant": "tenant-1",
        "payload": {"currencies": ["NGN", "USD"], "offers": ["01", "02"]},
    }


async def test_billing_account_provisioning_fails_closed_on_invalid_receipt(monkeypatch):
    async def fake_request(*_args, **_kwargs):
        return {"accounts_provisioned": 0}

    monkeypatch.setattr(onboarding, "request", fake_request)
    monkeypatch.setattr(onboarding.activity, "heartbeat", lambda: None)
    with pytest.raises(ApplicationError):
        await onboarding.provision_billing_accounts(value())
