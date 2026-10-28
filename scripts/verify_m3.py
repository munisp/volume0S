#!/usr/bin/env python3
"""Cluster-independent semantic checks for the M3 data and geospatial slice."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TABLES = {
    "br_events_usage": ("bronze", "kafka-bronze-sink"),
    "br_events_message": ("bronze", "kafka-bronze-sink"),
    "br_census_pings": ("bronze", "kafka-bronze-sink"),
    "br_scrape_runs": ("bronze", "scrape-connector"),
    "br_cdc_*": ("bronze", "debezium-bronze-sink"),
    "sl_dim_tenant": ("silver", "spark-conform"),
    "sl_dim_lead": ("silver", "spark-conform"),
    "sl_dim_poi": ("silver", "spark-conform-sedona"),
    "sl_fact_message": ("silver", "spark-conform"),
    "sl_fact_usage": ("silver", "spark-conform"),
    "sl_fact_payment": ("silver", "spark-conform"),
    "sl_fact_ai_job": ("silver", "spark-conform"),
    "gt_campaign_perf": ("gold", "spark-build-marts"),
    "gt_territory_scores": ("gold", "spark-sedona-ray-build"),
    "gt_lga_density": ("gold", "spark-sedona-build"),
    "gt_client_health": ("gold", "spark-build-marts"),
    "gt_cost_per_client": ("gold", "spark-build-marts"),
    "gt_revenue_by_offer": ("gold", "spark-build-marts"),
    "gt_model_registry": ("gold", "ray-model-registry"),
}

REQUIRED_FILES = (
    "apps/data/lakehouse/contracts.py",
    "apps/data/lakehouse/ingest.py",
    "apps/data/lakehouse/conform.py",
    "apps/data/lakehouse/gold.py",
    "apps/data/lakehouse/jobs/spark_etl.py",
    "apps/data/streaming/watches.py",
    "apps/data/geospatial/spatial.py",
    "apps/data/geospatial/sedona_job.py",
    "apps/data/ray-jobs/registry.py",
    "apps/data/ray-jobs/ray_job.py",
    "apps/core/svc-query/src/query.rs",
    "apps/core/svc-census-sync/sync.py",
    "apps/data/media-security/envelope.py",
    "apps/data/report-search/search.py",
)


def verify(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            errors.append(f"missing M3 component {relative}")

    catalog_path = root / "apps/data/lakehouse/catalog.json"
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"unreadable medallion catalog: {exc}"]
    rows = catalog.get("tables", [])
    tables = {row.get("name"): row for row in rows if isinstance(row, dict)}
    if len(tables) != len(rows):
        errors.append("medallion catalog contains duplicate or malformed tables")
    if set(tables) != set(EXPECTED_TABLES):
        errors.append("medallion catalog does not exactly match the reviewed table inventory")
    for name, (zone, owner) in EXPECTED_TABLES.items():
        row = tables.get(name, {})
        if row.get("zone") != zone or row.get("owner") != owner:
            errors.append(f"{name}: engine/zone ownership drifted from the reviewed boundary")
        if not row.get("required_columns") or not row.get("partitions") or not row.get("retention"):
            errors.append(f"{name}: schema, partitioning, or retention is incomplete")
        if name.startswith("br_") and row.get("cadence") == "in-place-update":
            errors.append(f"{name}: bronze must remain append-only")

    # Keep client-facing query and model-publication boundaries explicit. These
    # are source invariants, not evidence that deployed engines are healthy.
    query = (root / "apps/core/svc-query/src/query.rs").read_text(encoding="utf-8")
    for contract in ("MAX_ROWS: u32 = 1_000", "MAX_DAYS: i64 = 92", "`tenant_id` = '{tenant_id}'"):
        if contract not in query:
            errors.append(f"DataFusion tenant/query bound missing: {contract}")
    registry = (root / "apps/data/ray-jobs/registry.py").read_text(encoding="utf-8")
    for contract in ("approved", "model_version", "weight_version", "hmac"):
        if contract not in registry:
            errors.append(f"reviewed model registry contract missing: {contract}")
    spatial = (root / "apps/data/geospatial/spatial.py").read_text(encoding="utf-8")
    if "latlng_to_cell" not in spatial or "digital_silence" not in spatial:
        errors.append("H3/digital-silence geospatial boundary is incomplete")
    return errors


def main() -> int:
    errors = verify()
    if errors:
        print("\n".join(f"ERROR {error}" for error in errors), file=sys.stderr)
        return 1
    print(f"OK: {len(EXPECTED_TABLES)} medallion tables, engine ownership, tenant query bounds, and reviewed model gates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
