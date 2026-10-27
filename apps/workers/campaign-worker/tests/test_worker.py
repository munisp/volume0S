import pytest
from temporalio.exceptions import ApplicationError

import main as campaign
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


async def test_metric_failures_do_not_void_a_dispatched_campaign(monkeypatch):
    value = CampaignInput("tenant-a", "campaign-a", "Launch", 55_000_00, cycle_id="cycle-1")
    outcome = campaign.DispatchOutcome(
        "dispatch-1", "cycle-1", [campaign.DispatchChannelResult("email", 10, 0)],
    )
    settlements = []

    class Logger:
        def warning(self, *_args):
            pass

    async def wait_condition(_condition, **_kwargs):
        return None

    async def execute_activity(activity, args=None, **_kwargs):
        arguments = args if isinstance(args, list) else [args]
        if activity is campaign.draft_campaign:
            return "draft-1"
        if activity in {campaign.record_campaign_approval, campaign.record_campaign_cycle}:
            raise RuntimeError("metrics unavailable")
        if activity is campaign.reserve_budget:
            return "reservation-1"
        if activity is campaign.dispatch_campaign:
            return outcome
        if activity is campaign.settle_budget:
            settlements.append(arguments[2])
            return None
        raise AssertionError(f"unexpected activity: {activity}")

    monkeypatch.setattr(campaign.workflow, "logger", Logger())
    monkeypatch.setattr(campaign.workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(campaign.workflow, "execute_activity", execute_activity)
    instance = campaign.CampaignWorkflow()
    instance._approval = campaign.Approval("approval-1", "reviewer-1")

    assert await instance.run(value) == "dispatched:dispatch-1"
    assert settlements == ["post"]
