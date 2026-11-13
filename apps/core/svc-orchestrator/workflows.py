"""Temporal workflow boundary; no parallel queue or business-state store."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from typing import Any

from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import WorkflowIDReusePolicy
from temporalio.service import RPCError, RPCStatusCode
from temporalio.exceptions import TemporalError

QUEUE_LIMIT = 100
# A query is answered by a worker replaying the workflow. With its worker stopped, a query waits until the RPC gives
# up, and one such workflow used to fail (or time out) the whole list. Each query now has its own short limit.
QUERY_SECONDS = 4.0
QUERY_CONCURRENCY = 10
_ID_FIELD = {"campaign": "campaign_id", "studio": "pack_id", "offer": "job_id", "engagement": "engagement_id", "billing": "subscription_id"}


def unavailable(workflow_id: str, execution: Any, reason: str) -> dict[str, Any]:
    """What a list shows for a workflow whose worker can't answer: identity and dates from Temporal, no invented state."""
    kind, tenant_id, rest = (workflow_id.split(":", 2) + ["", ""])[:3]
    started = getattr(execution, "start_time", None)
    stamp = started.isoformat() if started else ""
    return {"tenant_id": tenant_id, _ID_FIELD.get(kind, "id"): rest, "status": "unavailable", "state": "unavailable",
            "error": reason, "started_at": stamp, "updated_at": stamp}

# A closed execution still answers the "status" query by replay, with whatever status it last set itself. One that
# failed mid-step (e.g. a worker crash while planning) therefore reported "planning" forever.
_ENDED_WITHOUT_FINISHING = {
    WorkflowExecutionStatus.FAILED: "failed",
    WorkflowExecutionStatus.TERMINATED: "failed",
    WorkflowExecutionStatus.TIMED_OUT: "failed",
    WorkflowExecutionStatus.CANCELED: "cancelled",
}


def settle(snapshot: dict[str, Any], execution_status: WorkflowExecutionStatus | None) -> dict[str, Any]:
    """Overlay Temporal's own verdict on a snapshot from an execution that ended without finishing."""
    outcome = _ENDED_WITHOUT_FINISHING.get(execution_status) if execution_status is not None else None
    if outcome and snapshot.get("status") not in ("failed", "cancelled"):
        return {**snapshot, "status": outcome, "stalled_status": snapshot.get("status"),
                "error": snapshot.get("error") or f"workflow {execution_status.name.lower()}"}
    return snapshot


class TemporalWorkflows:
    def __init__(self, client: Client):
        self.client = client

    async def ready(self) -> None:
        if not await self.client.service_client.check_health():
            raise RuntimeError("Temporal frontend health check failed")

    async def start(self, workflow_type: str, argument: dict[str, Any],
                    workflow_id: str, task_queue: str, retry_failed: bool = False) -> None:
        # retry_failed lets a FAILED run be started again under the same id (never a running or completed one);
        # only for workflows whose every side effect is keyed by deterministic ids, so a rerun can't double-charge.
        await self.client.start_workflow(
            workflow_type, argument, id=workflow_id, task_queue=task_queue,
            id_reuse_policy=(WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY if retry_failed
                             else WorkflowIDReusePolicy.REJECT_DUPLICATE),
        )

    async def query(self, workflow_id: str, run_id: str | None = None,
                    execution_status: WorkflowExecutionStatus | None = None) -> dict[str, Any]:
        handle = self.client.get_workflow_handle(workflow_id, run_id=run_id)
        if execution_status is None:
            execution_status = (await handle.describe()).status
        result = await handle.query("status")
        if is_dataclass(result):
            result = asdict(result)
        if not isinstance(result, dict):
            raise RuntimeError("workflow returned an invalid status snapshot")
        return settle(result, execution_status)

    async def list(self, prefix: str) -> list[dict[str, Any]]:
        # SAFE_ID validation keeps the tenant prefix free of query syntax.
        executions = []
        async for execution in self.client.list_workflows(f'WorkflowId STARTS_WITH "{prefix}"'):
            executions.append(execution)
            if len(executions) >= QUEUE_LIMIT:
                break
        gate = asyncio.Semaphore(QUERY_CONCURRENCY)

        async def one(execution: Any) -> dict[str, Any] | None:
            async with gate:
                try:
                    return await asyncio.wait_for(self.query(execution.id, execution.run_id, execution.status), QUERY_SECONDS)
                except RPCError as exc:
                    if exc.status == RPCStatusCode.NOT_FOUND:
                        return None  # A closed/evicted execution can disappear between list and query.
                    return unavailable(execution.id, execution, "Its worker didn't answer, so the current status can't be read.")
                except (asyncio.TimeoutError, TimeoutError, TemporalError):
                    # TemporalError covers a query the workflow itself failed or rejected.
                    return unavailable(execution.id, execution, "Its worker didn't answer, so the current status can't be read.")

        results = await asyncio.gather(*(one(execution) for execution in executions))
        return [snapshot for snapshot in results if snapshot is not None]

    async def signal(self, workflow_id: str, signal: str, argument: dict[str, Any]) -> None:
        await self.client.get_workflow_handle(workflow_id).signal(signal, argument)
