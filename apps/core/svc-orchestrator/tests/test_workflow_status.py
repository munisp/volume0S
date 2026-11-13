"""A workflow that ended without finishing is reported as failed, not by its last self-set status (2026-11-09).

Engagements whose worker crashed while planning were Failed in Temporal but answered "planning" forever: a closed
execution still answers the status query by replay."""
from temporalio.client import WorkflowExecutionStatus as S

from workflows import settle

PLANNING = {"engagement_id": "e1", "tenant_id": "t1", "status": "planning"}


def test_failed_terminated_timed_out_read_as_failed():
    for status in (S.FAILED, S.TERMINATED, S.TIMED_OUT):
        out = settle(PLANNING, status)
        assert out["status"] == "failed" and out["stalled_status"] == "planning" and out["error"]
        assert out["tenant_id"] == "t1"  # tenant checks still see the snapshot's own scope


def test_cancelled_reads_as_cancelled():
    assert settle(PLANNING, S.CANCELED)["status"] == "cancelled"


def test_running_and_completed_are_untouched():
    assert settle(PLANNING, S.RUNNING) == PLANNING
    done = {**PLANNING, "status": "approved"}
    assert settle(done, S.COMPLETED) == done
    assert settle(PLANNING, None) == PLANNING


def test_a_snapshot_that_already_says_failed_keeps_its_own_error():
    own = {**PLANNING, "status": "failed", "error": "plan rejected by policy"}
    assert settle(own, S.FAILED) == own
