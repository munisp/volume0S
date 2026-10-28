"""Spark + Sedona batch entry point; never used for interactive serving."""
from __future__ import annotations

from typing import Any

import h3


def attach_boundaries(spark: Any, poi_view: str, boundary_view: str, output_view: str) -> None:
    """Attach H3 indices and exactly one LGA/ward/corridor through ST_Contains."""
    spark.udf.register("volumeos_h3_r9", lambda lat, lon: h3.latlng_to_cell(lat, lon, 9))
    spark.udf.register("volumeos_h3_r7", lambda lat, lon: h3.latlng_to_cell(lat, lon, 7))
    spark.sql(f"""
        CREATE OR REPLACE TEMP VIEW `{output_view}` AS
        SELECT p.*, ST_Point(p.longitude, p.latitude) AS geometry,
               volumeos_h3_r9(p.latitude, p.longitude) AS h3_r9,
               volumeos_h3_r7(p.latitude, p.longitude) AS h3_r7,
               b.lga, b.ward, b.corridor
        FROM `{poi_view}` p
        LEFT JOIN `{boundary_view}` b
          ON ST_Contains(b.geometry, ST_Point(p.longitude, p.latitude))
    """)
    duplicated = spark.sql(f"""
        SELECT poi_id FROM `{output_view}` GROUP BY poi_id HAVING COUNT(*) > 1
    """)
    if duplicated.head(1):
        raise RuntimeError("overlapping authoritative boundaries would duplicate POIs")


def optimize_spatial_table(spark: Any, table: str) -> None:
    if table not in {"sl_dim_poi", "gt_lga_density", "gt_territory_scores"}:
        raise ValueError("table is not an approved Sedona spatial target")
    spark.sql(f"OPTIMIZE `{table}` ZORDER BY (corridor, lga)")
