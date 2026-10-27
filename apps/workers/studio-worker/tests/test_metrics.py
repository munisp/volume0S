from datetime import datetime, timedelta, timezone

import pytest

import metrics
from studio_main import StudioResult, record_studio_outcome


def sample(name, labels):
    for metric in metrics.REGISTRY.collect():
        for point in metric.samples:
            if point.name == name and point.labels == labels:
                return point.value
    return None


def test_terminal_outcomes_include_duration_and_map_qa_timeout_to_failed():
    started = datetime.now(timezone.utc) - timedelta(seconds=5)
    before = sample("volumeos_studio_packs_total", {"result": "failed"}) or 0
    record_studio_outcome(StudioResult("pack-1", "qa_timed_out", []), started)
    assert sample("volumeos_studio_packs_total", {"result": "failed"}) == before + 1
    assert sample("volumeos_studio_pack_duration_seconds_count", {"result": "failed"}) >= 1


def test_outcome_metrics_reject_unknown_status_and_negative_duration():
    with pytest.raises(ValueError):
        metrics.record_outcome("approved", 1)
    with pytest.raises(ValueError):
        metrics.record_outcome("delivered", -1)
