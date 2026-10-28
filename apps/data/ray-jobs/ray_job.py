"""Ray entry points; Ray owns training/scoring, never Spark ETL or CRM writes."""
from __future__ import annotations

from typing import Any

from registry import ModelRegistry
from territory import TerritoryWeights, batch_score, refine_by_cell


def train_and_review_candidate(samples: list[dict[str, float]], prior: TerritoryWeights,
                               version: str, registry: ModelRegistry,
                               metrics: dict[str, Any], lineage_id: str,
                               minimum_observations: int = 30) -> dict[str, TerritoryWeights]:
    weights = refine_by_cell(samples, prior, version, minimum_observations)
    registry.propose(
        "territory-score", version, version,
        {"minimum_observations": minimum_observations, "prior_version": prior.version},
        metrics, lineage_id,
    )
    return weights


def score_approved_batch(features: list[dict[str, float]], weights: dict[str, TerritoryWeights],
                         prior: TerritoryWeights, registry: ModelRegistry,
                         version: str) -> list[dict[str, Any]]:
    scores = batch_score(features, weights, prior)
    return registry.crm_sync("territory-score", version, version, scores)


def ray_remote_functions() -> tuple[Any, Any]:
    """Bind remote wrappers only inside the Ray job, not in local contract tests."""
    import ray

    return ray.remote(train_and_review_candidate), ray.remote(score_approved_batch)
