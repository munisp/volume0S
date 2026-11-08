"""The worker registers these workflows with Temporal, which re-imports their modules inside its deterministic sandbox
and refuses to start if a module touches something the sandbox forbids (as boto3's HTTP stack did). Run the same check
here so it fails the build instead of the worker."""
from __future__ import annotations

import pytest
from temporalio import workflow
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

import worker


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow_class", [worker.OfferWorkflow, worker.EngagementWorkflow])
async def test_workflow_passes_temporal_sandbox_validation(workflow_class):
    SandboxedWorkflowRunner().prepare_workflow(workflow._Definition.must_from_class(workflow_class))
