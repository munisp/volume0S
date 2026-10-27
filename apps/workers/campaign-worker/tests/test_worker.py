import pytest
from temporalio.exceptions import ApplicationError

from worker import CampaignInput, redact_pii, validate_campaign


def test_redacts_nigerian_phone_and_email_before_ai():
    text = "Call 08031234567 or +2347031234567; email owner@example.ng"
    redacted = redact_pii(text)
    assert "08031234567" not in redacted
    assert "+2347031234567" not in redacted
    assert "owner@example.ng" not in redacted
    assert redacted.count("[redacted-phone]") == 2


def test_rejects_unfunded_campaign():
    with pytest.raises(ApplicationError):
        validate_campaign(CampaignInput("tenant-a", "campaign-a", "Launch", 0, cycle_id="cycle-1"))


def test_accepts_valid_campaign():
    validate_campaign(CampaignInput("tenant-a", "campaign-a", "Launch", 55_000_00, cycle_id="cycle-1"))
