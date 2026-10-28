import json
from datetime import datetime, timedelta, timezone

import pytest
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from contracts import ClosedDayRollup, RollupError
from dispatcher import RollupDispatcher, dispatch
from rollup_job import ROLLUP_SQL


def payload(**changes):
    value = {
        "tenant_id": "tenant-a",
        "provider": "termii",
        "channel": "sms",
        "campaign_id": "campaign-1",
        "offer": "02",
        "day": "2026-10-25",
        "event_count": "25",
        "amount_minor": "12500",
        "provider_cost_minor": "9000",
        "currency": "NGN",
    }
    value.update(changes)
    return value


class FakeHandle:
    def __init__(self, client):
        self.client = client

    async def query(self, name):
        assert name == "status"
        return self.client.recorded


class FakeClient:
    def __init__(self):
        self.calls = []
        self.recorded = None

    async def start_workflow(self, workflow, value, **options):
        self.calls.append((workflow, value, options))
        if self.recorded is not None:
            raise WorkflowAlreadyStartedError(options["id"], workflow)
        self.recorded = {"state": "collecting", "days": [dict(value)]}

    def get_workflow_handle(self, workflow_id):
        assert workflow_id == self.calls[-1][2]["id"]
        return FakeHandle(self)


def test_contract_preserves_exact_amounts_and_closed_day_boundary():
    rollup = ClosedDayRollup.parse(payload())
    assert rollup.event_count == 25
    assert rollup.amount_minor == 12_500
    assert rollup.provider_cost_minor == 9_000
    delay = timedelta(hours=48)
    assert not rollup.is_closed(datetime(2026, 10, 27, 23, 59, tzinfo=timezone.utc), delay)
    assert rollup.is_closed(datetime(2026, 10, 28, 0, 0, tzinfo=timezone.utc), delay)
    workflow_input = rollup.workflow_input(timedelta(hours=48))
    assert workflow_input["provider"] == "termii"
    assert workflow_input["offer"] == "02"
    assert workflow_input["provider_cost_minor"] == 9000
    assert rollup.week == "2026-W43"


@pytest.mark.parametrize("change", [
    {"amount_minor": -1}, {"event_count": 0}, {"tenant_id": "bad tenant"},
    {"currency": "EUR"}, {"day": "not-a-date"}, {"extra": True},
])
def test_contract_rejects_invalid_or_unversioned_rollups(change):
    with pytest.raises(RollupError):
        ClosedDayRollup.parse(payload(**change))


async def test_duplicate_workflow_is_verified_not_silently_ignored():
    client = FakeClient()
    rollup = ClosedDayRollup.parse(payload())
    assert await dispatch(client, rollup) == "started"
    assert await dispatch(client, rollup) == "verified"
    assert len(client.calls) == 2
    options = client.calls[0][2]
    assert options["id_reuse_policy"] == WorkflowIDReusePolicy.REJECT_DUPLICATE
    assert options["task_queue"] == "billing-v1"
    client.recorded["days"][0]["amount_minor"] += 1
    with pytest.raises(RollupError, match="changed after posting"):
        await dispatch(client, rollup)


async def test_open_rollup_updates_replace_pending_value_before_close():
    client = FakeClient()
    dispatcher = RollupDispatcher(client, timedelta(hours=48))
    early = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    assert (await dispatcher.ingest(json.dumps(payload()).encode(), early))["open"] == 1
    updated = payload(event_count=30, amount_minor=15000, provider_cost_minor=10000)
    assert (await dispatcher.ingest(json.dumps(updated).encode(), early))["open"] == 1
    assert len(client.calls) == 0
    closed = datetime(2026, 10, 28, 0, 0, tzinfo=timezone.utc)
    assert (await dispatcher.reconcile(closed))["started"] == 1
    assert client.recorded["days"][0]["event_count"] == 30
    assert client.recorded["days"][0]["amount_minor"] == 15_000


def test_flink_sql_deduplicates_event_ids_and_separates_charge_from_cost():
    assert "PARTITION BY event_id" in ROLLUP_SQL
    assert "SUM(charge_minor) AS amount_minor" in ROLLUP_SQL
    assert "SUM(cost_minor) AS provider_cost_minor" in ROLLUP_SQL
    assert "charge_minor > 0" in ROLLUP_SQL
