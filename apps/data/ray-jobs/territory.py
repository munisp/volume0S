"""Ray-callable territory-weight refinement and explainable batch scoring."""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class TerritoryWeights:
    version: str
    density: float
    digital_silence: float
    value: float
    competition: float

    def __post_init__(self) -> None:
        if not self.version or not all(math.isfinite(value) for value in self.vector):
            raise ValueError("model version and finite weights are required")
        if any(abs(value) > 100 for value in self.vector):
            raise ValueError("weight exceeds the reviewed safety bound")

    @property
    def vector(self) -> tuple[float, float, float, float]:
        return self.density, self.digital_silence, self.value, self.competition

    def score(self, feature: dict[str, float]) -> float:
        # Density is log-scaled; the remaining signals are already normalized.
        values = (math.log1p(max(0.0, feature["density"])), feature["digital_silence"],
                  feature["value"], feature["competition"])
        return sum(weight * value for weight, value in zip(self.vector, values))


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    size = len(vector)
    augmented = [matrix[row][:] + [vector[row]] for row in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        if abs(divisor) < 1e-12:
            raise ValueError("training matrix is singular")
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [a - factor * b for a, b in zip(augmented[row], augmented[column])]
    return [augmented[row][-1] for row in range(size)]


def _fit(samples: list[dict[str, float]], prior: TerritoryWeights, version: str) -> TerritoryWeights:
    ridge = 1.0
    matrix = [[0.0] * 4 for _ in range(4)]
    vector = [0.0] * 4
    for sample in samples:
        features = (math.log1p(max(0.0, sample["density"])), sample["digital_silence"],
                    sample["value"], sample["competition"])
        target = float(sample["conversion"])
        if not 0 <= target <= 1:
            raise ValueError("conversion target must be normalized")
        for row in range(4):
            vector[row] += features[row] * target
            for column in range(4):
                matrix[row][column] += features[row] * features[column]
    for index in range(4):
        matrix[index][index] += ridge
        vector[index] += ridge * prior.vector[index]
    weights = _solve(matrix, vector)
    if any(not math.isfinite(value) for value in weights):
        raise ValueError("Ray training produced non-finite weights")
    return TerritoryWeights(version, *(max(-100.0, min(100.0, value)) for value in weights))


def refine_by_cell(samples: list[dict[str, float]], prior: TerritoryWeights,
                   version: str, minimum_observations: int = 30) -> dict[str, TerritoryWeights]:
    if minimum_observations < 1 or not version or version == prior.version:
        raise ValueError("new weight version and positive observation guard are required")
    cells: dict[str, list[dict[str, float]]] = defaultdict(list)
    for sample in samples:
        cells[str(sample["h3_r9"])].append(sample)
    return {
        cell: _fit(cell_samples, prior, version) if len(cell_samples) >= minimum_observations
        else TerritoryWeights(version, *prior.vector)
        for cell, cell_samples in sorted(cells.items())
    }


def batch_score(features: list[dict[str, float]], weights: dict[str, TerritoryWeights],
                prior: TerritoryWeights) -> list[dict[str, float | str]]:
    scores = []
    for feature in features:
        cell = str(feature["h3_r9"])
        selected = weights.get(cell, prior)
        scores.append({
            "h3_r9": cell,
            "score": selected.score(feature),
            "model_version": selected.version,
            "weight_version": selected.version,
        })
    return scores
