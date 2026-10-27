import pytest
from temporalio.exceptions import ApplicationError

import metrics
from main import CampaignInput, parse_dispatch_outcome, record_campaign_cycle


def campaign():
    return CampaignInput("tenant-a", "campaign-a", "Launch", 1000, cycle_id="cycle-1")


def sample(name, labels):
    for metric in metrics.REGISTRY.collect():
        for point in metric.samples:
            if point.name == name and point.labels == labels:
                return point.value
    return None


def test_cycle_metrics_are_absolute_and_retry_safe():
    metrics.record_cycle("tenant-a", "campaign-a", "cycle-1", "email", 8, 2)
    metrics.record_cycle("tenant-a", "campaign-a", "cycle-1", "email", 9, 1)
    labels = {"tenant_id": "tenant-a", "campaign_id": "campaign-a", "cycle_id": "cycle-1", "channel": "email"}
    assert sample("volumeos_campaign_cycle_dispatches", labels | {"result": "success"}) == 9
    assert sample("volumeos_campaign_cycle_dispatches", labels | {"result": "failure"}) == 1
    cycle_labels = {"tenant_id": "tenant-a", "campaign_id": "campaign-a", "cycle_id": "cycle-1"}
    assert sample("volumeos_campaign_cycle_completed_timestamp_seconds", cycle_labels) > 0


@pytest.mark.parametrize("success,failure", [(-1, 2), (True, 2), (0, 0), (1.5, 2)])
def test_cycle_metrics_reject_invalid_counts(success, failure):
    with pytest.raises(ValueError):
        metrics.record_cycle("tenant-a", "campaign-a", "cycle-1", "email", success, failure)


def test_dispatch_contract_requires_matching_reconciled_cycle():
    outcome = parse_dispatch_outcome(campaign(), {
        "dispatch_id": "dispatch-1", "cycle_id": "cycle-1",
        "channels": [{"channel": "email", "success": 9, "failure": 1}],
    })
    record_campaign_cycle(campaign(), outcome)
    assert outcome.dispatch_id == "dispatch-1"
    for body in (
        {"dispatch_id": "dispatch-1", "cycle_id": "other", "channels": [{"channel": "email", "success": 1, "failure": 0}]},
        {"dispatch_id": "dispatch-1", "cycle_id": "cycle-1", "channels": []},
        {"dispatch_id": "dispatch-1", "cycle_id": "cycle-1", "channels": [{"channel": "email", "success": 0, "failure": 0}]},
        {"dispatch_id": "dispatch-1", "cycle_id": "cycle-1", "channels": [{"channel": "email", "success": True, "failure": 0}]},
    ):
        with pytest.raises(ApplicationError):
            parse_dispatch_outcome(campaign(), body)


def test_approval_and_latency_labels_are_bounded():
    metrics.observe_approval("approved")
    metrics.observe_dispatch("email", "success", 0.25)
    with pytest.raises(ValueError):
        metrics.observe_approval("perhaps")
    with pytest.raises(ValueError):
        metrics.observe_dispatch("email", "accepted", 0.25)
