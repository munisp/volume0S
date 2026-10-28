import math

import pytest

from registry import InMemoryRegistryStore, ModelRegistry
from territory import TerritoryWeights, batch_score, refine_by_cell

PRIOR = TerritoryWeights("weights-v1", 0.2, 0.4, 0.3, -0.1)


def sample(cell="cell-a", conversion=0.5):
    return {
        "h3_r9": cell, "density": 10.0, "digital_silence": 0.8,
        "value": 0.7, "competition": 0.2, "conversion": conversion,
    }


def feature(cell="cell-a"):
    return {"h3_r9": cell, "density": 10.0, "digital_silence": 0.8, "value": 0.7, "competition": 0.2}


def test_scores_are_explainable_from_the_four_versioned_weights():
    score = PRIOR.score(feature())
    expected = 0.2 * math.log1p(10) + 0.4 * 0.8 + 0.3 * 0.7 - 0.1 * 0.2
    assert score == pytest.approx(expected)
    row = batch_score([feature()], {}, PRIOR)[0]
    assert row["score"] == pytest.approx(expected)
    assert row["model_version"] == "weights-v1"


def test_cells_below_minimum_history_keep_prior_weights():
    weights = refine_by_cell([sample()], PRIOR, "weights-v2", minimum_observations=2)
    assert weights["cell-a"].vector == PRIOR.vector
    assert weights["cell-a"].version == "weights-v2"
    fitted = refine_by_cell([sample(conversion=0.2), sample(conversion=0.8)], PRIOR, "weights-v2", 2)
    assert fitted["cell-a"].vector != PRIOR.vector


def make_registry():
    store = InMemoryRegistryStore()
    registry = ModelRegistry(store, b"r" * 32)
    record = registry.propose(
        "territory-score", "model-v2", "weights-v2", {"min_obs": 30},
        {"auc": 0.71, "max_score_delta": 0.2}, "ray-run-7",
    )
    return registry, record


def test_crm_sync_is_blocked_until_the_exact_version_has_signed_human_approval():
    registry, record = make_registry()
    scores = [{"h3_r9": "cell-a", "score": 0.7, "model_version": "model-v2", "weight_version": "weights-v2"}]
    with pytest.raises(ValueError):
        registry.crm_sync("territory-score", "model-v2", "weights-v2", scores)
    with pytest.raises(ValueError):
        registry.review("territory-score", "model-v2", "reviewer-a", "approved", "0" * 64)
    signature = registry.sign_review(record, "reviewer-a", "approved")
    reviewed = registry.review("territory-score", "model-v2", "reviewer-a", "approved", signature)
    assert reviewed["review_state"] == "approved"
    synced = registry.crm_sync("territory-score", "model-v2", "weights-v2", scores)
    assert synced[0]["review_record_sha256"] == registry.record_digest(record)
    with pytest.raises(ValueError):
        registry.crm_sync("territory-score", "model-v2", "weights-v1", scores)
    with pytest.raises(ValueError):
        registry.review("territory-score", "model-v2", "reviewer-a", "approved", signature)


def test_registry_records_cannot_be_rewritten_and_rejection_is_terminal():
    registry, record = make_registry()
    with pytest.raises(ValueError):
        registry.store.put_if_absent({**record, "metrics_json": '{"auc":0.99}'})
    signature = registry.sign_review(record, "reviewer-a", "rejected")
    registry.review("territory-score", "model-v2", "reviewer-a", "rejected", signature)
    with pytest.raises(ValueError):
        registry.crm_sync("territory-score", "model-v2", "weights-v2", [])
