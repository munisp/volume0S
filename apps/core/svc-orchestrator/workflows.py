"""Temporal workflow boundary; no parallel queue or business-state store."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import WorkflowIDReusePolicy
from temporalio.service import RPCError, RPCStatusCode

QUEUE_LIMIT = 100

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
                    workflow_id: str, task_queue: str) -> None:
        await self.client.start_workflow(
            workflow_type, argument, id=workflow_id, task_queue=task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
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
        snapshots = []
        # SAFE_ID validation keeps the tenant prefix free of query syntax.
        executions = self.client.list_workflows(f'WorkflowId STARTS_WITH "{prefix}"')
        async for execution in executions:
            try:
                snapshots.append(await self.query(execution.id, execution.run_id, execution.status))
            except RPCError as exc:
                if exc.status != RPCStatusCode.NOT_FOUND:
                    raise
                # A closed/evicted execution can disappear between list and query.
                continue
            if len(snapshots) >= QUEUE_LIMIT:
                break
        return snapshots

    async def signal(self, workflow_id: str, signal: str, argument: dict[str, Any]) -> None:
        await self.client.get_workflow_handle(workflow_id).signal(signal, argument)
