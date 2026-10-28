"""Fail-closed data-quality gates evaluated before any gold write."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from catalog import Catalog


@dataclass(frozen=True)
class QualityPolicy:
    max_row_count_delta: float = 0.5
    max_null_rate: float = 0.0
    max_dedup_rate: float = 0.02


@dataclass(frozen=True)
class QualityReport:
    passed: bool
    failures: tuple[str, ...]
    row_count_delta: float
    dedup_rate: float


def evaluate_quality(table: str, rows: list[dict[str, Any]], prior_row_count: int,
                     duplicate_count: int = 0, policy: QualityPolicy = QualityPolicy(),
                     catalog: Catalog | None = None) -> QualityReport:
    spec = (catalog or Catalog.load()).table(table)
    if spec.zone != "gold":
        raise ValueError("quality publication gates apply to gold tables")
    failures: list[str] = []
    if prior_row_count:
        delta = abs(len(rows) - prior_row_count) / prior_row_count
    else:
        delta = 0.0 if rows else 1.0
    if delta > policy.max_row_count_delta:
        failures.append(f"row-count delta {delta:.4f} exceeds {policy.max_row_count_delta:.4f}")
    total = len(rows) + duplicate_count
    dedup_rate = duplicate_count / total if total else 0.0
    if dedup_rate >= policy.max_dedup_rate:
        failures.append(f"dedup rate {dedup_rate:.4f} must be below {policy.max_dedup_rate:.4f}")
    for index, row in enumerate(rows):
        for column in spec.required_columns:
            if column not in row or row[column] is None or row[column] == "":
                failures.append(f"row {index} has null/blank key column {column}")
        if table == "gt_territory_scores" and not row.get("geometry"):
            failures.append(f"row {index} score has no geometry")
        if table == "gt_revenue_by_offer" and not row.get("tenant_id"):
            failures.append(f"row {index} financial fact has no tenant")
    if not rows:
        failures.append("gold build contains no rows")
    return QualityReport(not failures, tuple(failures), delta, dedup_rate)
