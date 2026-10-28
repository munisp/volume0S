"""Dispatch closed-day rollups into durable, idempotent ISO-week workflows."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from aiokafka import AIOKafkaConsumer
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from contracts import ClosedDayRollup, RollupError

LOG = logging.getLogger("volumeos.usage-dispatcher")


def _status_dict(recorded: Any) -> dict[str, Any]:
    if is_dataclass(recorded):
        recorded = asdict(recorded)
    return dict(recorded) if isinstance(recorded, dict) else {}


def _recorded_days(recorded: Any) -> list[dict[str, Any]]:
    days = _status_dict(recorded).get("days", [])
    if not isinstance(days, list):
        return []
    result = []
    for day in days:
        if is_dataclass(day):
            day = asdict(day)
        if isinstance(day, dict):
            result.append(dict(day))
    return result


def same_rollup(recorded: Any, expected: ClosedDayRollup) -> bool:
    for recorded_day in _recorded_days(recorded):
        if recorded_day.get("day") != expected.day:
            continue
        recorded_day.pop("close_delay_seconds", None)
        try:
            return ClosedDayRollup.parse(recorded_day) == expected
        except RollupError:
            return False
    return False


async def dispatch(
    client: Any, rollup: ClosedDayRollup, close_delay: timedelta = timedelta(hours=48)
) -> str:
    try:
        await client.start_workflow(
            "UsageRollupWorkflow",
            rollup.workflow_input(close_delay),
            id=rollup.workflow_id,
            task_queue="billing-v1",
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
        return "started"
    except WorkflowAlreadyStartedError:
        handle = client.get_workflow_handle(rollup.workflow_id)
        recorded = await handle.query("status")
        if same_rollup(recorded, rollup):
            return "verified"
        days = _recorded_days(recorded)
        if any(day.get("day") == rollup.day for day in days):
            raise RollupError(f"closed rollup changed after posting: {rollup.workflow_id}")
        if _status_dict(recorded).get("state") != "collecting":
            raise RollupError(f"late rollup arrived after week close: {rollup.workflow_id}")
        await handle.signal("record_day", rollup.workflow_input(close_delay))
        return "signaled"


class RollupDispatcher:
    """Replay compacted Kafka state on restart; retain open days in memory."""

    def __init__(self, client: Any, close_delay: timedelta):
        self.client = client
        self.close_delay = close_delay
        self.pending: dict[str, ClosedDayRollup] = {}

    async def reconcile(self, now: datetime | None = None) -> dict[str, int]:
        current = now or datetime.now(timezone.utc)
        outcomes = {"started": 0, "verified": 0, "signaled": 0, "open": 0}
        for key, rollup in list(self.pending.items()):
            if not rollup.is_closed(current, self.close_delay):
                outcomes["open"] += 1
                continue
            outcome = await dispatch(self.client, rollup, self.close_delay)
            outcomes[outcome] += 1
            del self.pending[key]
        return outcomes

    async def ingest(self, payload: bytes, now: datetime | None = None) -> dict[str, int]:
        try:
            raw = json.loads(payload)
            rollup = ClosedDayRollup.parse(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, RollupError) as error:
            raise RollupError("invalid usage rollup") from error
        key = rollup.pending_key
        previous = self.pending.get(key)
        if previous is not None and previous.workflow_id != rollup.workflow_id:
            raise RollupError("rollup identity changed its billing scope")
        self.pending[key] = rollup
        return await self.reconcile(now)


async def main() -> None:
    brokers = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "").strip()
    if not brokers:
        raise RuntimeError("KAFKA_BOOTSTRAP_SERVERS is required")
    delay_hours = int(os.environ.get("USAGE_CLOSE_DELAY_HOURS", "48"))
    if delay_hours < 1 or delay_hours > 168:
        raise RuntimeError("USAGE_CLOSE_DELAY_HOURS must be between 1 and 168")
    temporal = await Client.connect(
        os.environ.get("TEMPORAL_HOSTPORT", "temporal-frontend.ops.svc.cluster.local:7233"),
        namespace=os.environ.get("TEMPORAL_NAMESPACE", "volumeos"),
    )
    consumer = AIOKafkaConsumer(
        os.environ.get("KAFKA_ROLLUP_TOPIC", "usage.rollups"),
        bootstrap_servers=brokers,
        group_id=None,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        value_deserializer=lambda value: value,
    )
    dispatcher = RollupDispatcher(temporal, timedelta(hours=delay_hours))
    await consumer.start()
    try:
        while True:
            records = await consumer.getmany(timeout_ms=1000, max_records=500)
            for messages in records.values():
                for message in messages:
                    if message.value is not None:
                        outcomes = await dispatcher.ingest(message.value)
                        LOG.info("usage rollup reconciled: %s", outcomes)
            if not records:
                await dispatcher.reconcile()
    finally:
        await consumer.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
