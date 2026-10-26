import pytest
from temporalio.exceptions import ApplicationError

from onboarding import OnboardingInput, saga_compensations, validate_onboarding


def value(**changes):
    base = dict(tenant_id="tenant-1", name="Demo Gym", slug="demo-gym", corridor="lagos-yaba", offers=["01", "02"])
    base.update(changes)
    return OnboardingInput(**base)


def test_valid_m1_onboarding():
    validate_onboarding(value())


@pytest.mark.parametrize("changes", [
    {"tenant_id": "bad tenant"}, {"corridor": ""}, {"slug": "Bad Slug"},
    {"name": "x"}, {"offers": []}, {"offers": ["03"]},
])
def test_invalid_onboarding_fails_before_side_effects(changes):
    with pytest.raises(ApplicationError):
        validate_onboarding(value(**changes))


def test_compensation_is_reverse_and_excludes_read_only_steps():
    completed = ["identity", "tenancy", "listmonk", "postiz", "active", "welcome"]
    assert saga_compensations(completed) == ["undo:postiz", "undo:listmonk", "undo:tenancy", "undo:identity"]
    assert saga_compensations(["identity", "tenancy"]) == ["undo:tenancy", "undo:identity"]
    assert saga_compensations([]) == []


def test_unknown_saga_step_is_rejected():
    with pytest.raises(ValueError):
        saga_compensations(["identity", "ledger"])
