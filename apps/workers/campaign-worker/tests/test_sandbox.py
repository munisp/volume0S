"""The workflows must pass Temporal's sandbox validation: the worker refuses to start otherwise (2026-11-11 outage)."""
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
from temporalio.workflow import _Definition

from main import CampaignWorkflow
from onboarding import OnboardingWorkflow


async def test_workflows_load_in_the_temporal_sandbox():
    runner = SandboxedWorkflowRunner()
    for workflow_class in (CampaignWorkflow, OnboardingWorkflow):
        runner.prepare_workflow(_Definition.must_from_class(workflow_class))
