"""Temporal workflow boundary; no parallel queue or business-state store."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.service import RPCError, RPCStatusCode

QUEUE_LIMIT = 100


class TemporalWorkflows:
    def __init__(self, client: Client):
        self.client = client

    async def ready(self) -> None:
        await self.client.workflow_service.get_system_info()

    async def start(self, workflow_type: str, argument: dict[str, Any],
                    workflow_id: str, task_queue: str) -> None:
        await self.client.start_workflow(
            workflow_type, argument, id=workflow_id, task_queue=task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.WORKFLOW_ID_REUSE_POLICY_REJECT_DUPLICATE,
        )

    async def query(self, workflow_id: str, run_id: str | None = None) -> dict[str, Any]:
        handle = self.client.get_workflow_handle(workflow_id, run_id=run_id)
        result = await handle.query("status")
        if is_dataclass(result):
            return asdict(result)
        if not isinstance(result, dict):
            raise RuntimeError("workflow returned an invalid status snapshot")
        return result

    async def list(self, prefix: str) -> list[dict[str, Any]]:
        snapshots = []
        # SAFE_ID validation keeps the tenant prefix free of query syntax.
        executions = self.client.list_workflows(f'WorkflowId STARTS_WITH "{prefix}"')
        async for execution in executions:
            try:
                snapshots.append(await self.query(execution.id, execution.run_id))
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
