"""Spark/Delta bronze→silver→gold ETL and maintenance entry points.

Flink never writes these medallion tables. Ray supplies model/scoring artifacts;
this job owns tabular ETL, quality gates, and Delta maintenance.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog import Catalog
from gold import PII_COLUMNS

SHA256 = re.compile(r"^[0-9a-f]{64}$")

# Explicit payload allowlists prevent schema-as-arrived PII from becoming silver.
TRANSFORMS: dict[tuple[str, str], dict[str, str]] = {
    ("br_events_message", "sl_fact_message"): {
        "campaign_id": "string", "provider_message_id": "string", "status": "string", "channel": "string",
    },
    ("br_events_usage", "sl_fact_usage"): {
        "channel": "string", "campaign_id": "string", "amount_minor": "long",
        "provider_cost_minor": "long", "currency": "string", "offer": "string",
    },
    ("br_cdc_payments", "sl_fact_payment"): {
        "transfer_id": "string", "amount_minor": "long", "currency": "string", "state": "string", "offer": "string",
    },
    ("br_cdc_ai_jobs", "sl_fact_ai_job"): {
        "job_id": "string", "artefact_uri": "string", "cost_minor": "long", "currency": "string",
    },
    ("br_cdc_tenants", "sl_dim_tenant"): {"display_name": "string"},
    ("br_census_pings", "sl_dim_poi"): {
        "business_name": "string", "niche": "string", "phone_hash": "string", "notes": "string",
        "latitude": "double", "longitude": "double", "accuracy_m": "double",
        "consent_reference": "string", "lawful_basis": "string", "device_id": "string", "corridor": "string",
    },
}


def quality_decision(row_count: int, prior_row_count: int, duplicate_count: int,
                     null_key_count: int, max_row_delta: float = 0.5,
                     max_dedup_rate: float = 0.02) -> tuple[str, ...]:
    failures = []
    delta = abs(row_count - prior_row_count) / prior_row_count if prior_row_count else (0.0 if row_count else 1.0)
    total = row_count + duplicate_count
    dedup_rate = duplicate_count / total if total else 0.0
    if not row_count:
        failures.append("gold batch contains no rows")
    if delta > max_row_delta:
        failures.append("gold row-count delta exceeds the reviewed bound")
    if dedup_rate >= max_dedup_rate:
        failures.append("gold duplicate rate exceeds the reviewed bound")
    if null_key_count:
        failures.append("gold batch contains null or blank required keys")
    return tuple(failures)


class SparkMedallionJob:
    def __init__(self, spark: Any, root_uri: str, catalog: Catalog | None = None):
        if not root_uri.startswith(("s3://", "s3a://")):
            raise ValueError("lakehouse root must be an S3-compatible URI")
        self.spark = spark
        self.root_uri = root_uri.rstrip("/")
        self.catalog = catalog or Catalog.load()

    @staticmethod
    def create_session(app_name: str = "volumeos-medallion-etl") -> Any:
        from pyspark.sql import SparkSession
        from sedona.spark import SedonaContext

        spark = (SparkSession.builder.appName(app_name)
                 .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension,org.apache.sedona.sql.SedonaSqlExtensions")
                 .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
                 .getOrCreate())
        SedonaContext.create(spark)
        return spark

    def path(self, table: str) -> str:
        self.catalog.table(table)
        return f"{self.root_uri}/{table}"

    def read(self, table: str) -> Any:
        return self.spark.read.format("delta").load(self.path(table))

    def conform(self, bronze_table: str, silver_table: str) -> None:
        fields = TRANSFORMS.get((bronze_table, silver_table))
        if fields is None:
            raise ValueError("bronze/silver pair is not an approved Spark transformation")
        if self.catalog.table(bronze_table).zone != "bronze" or self.catalog.table(silver_table).zone != "silver":
            raise ValueError("Spark conformance requires bronze input and silver output")
        from pyspark.sql import functions as F
        from pyspark.sql import types as T
        from pyspark.sql.window import Window

        schema = T.StructType([T.StructField(name, T.LongType() if kind == "long" else
                                             T.DoubleType() if kind == "double" else T.StringType(), True)
                               for name, kind in fields.items()])
        dedup = Window.partitionBy("event_id").orderBy(F.col("ts_unix").desc(), F.col("lineage_id").desc())
        frame = (self.read(bronze_table).withColumn("dedup_rank", F.row_number().over(dedup))
                 .filter(F.col("dedup_rank") == 1)
                 .withColumn("payload", F.from_json("payload_json", schema))
                 .filter(F.col("payload").isNotNull())
                 .withColumn("effective_ts_unix", F.col("ts_unix"))
                 .withColumn("record_version", F.lit(1))
                 .withColumn("event_date", F.to_date(F.from_unixtime("ts_unix")))
                 .withColumn("year", F.year("event_date")).withColumn("month", F.month("event_date"))
                 .withColumn("day", F.dayofmonth("event_date"))
                 .withColumn("tenant_hash", F.pmod(F.xxhash64("tenant_id"), F.lit(32))))
        if silver_table == "sl_dim_poi":
            import h3
            self.spark.udf.register("volumeos_h3_r9", lambda lat, lon: h3.latlng_to_cell(lat, lon, 9))
            self.spark.udf.register("volumeos_h3_r7", lambda lat, lon: h3.latlng_to_cell(lat, lon, 7))
            payload = frame.select("payload.*").columns
            for required in ("latitude", "longitude", "phone_hash"):
                if required not in payload:
                    raise RuntimeError("census payload allowlist is incomplete")
            frame = frame.selectExpr(
                "tenant_id", "event_id AS poi_id", "event_id", "source", "lineage_id", "effective_ts_unix", "record_version",
                "payload.business_name", "payload.niche", "payload.phone_hash", "payload.notes",
                "payload.latitude", "payload.longitude", "payload.accuracy_m",
                "ST_AsText(ST_Point(payload.longitude, payload.latitude)) AS geometry",
                "'provided' AS geo_source", "1.0 AS geo_confidence", "payload.accuracy_m <= 25 AS scoring_eligible",
                "volumeos_h3_r9(payload.latitude, payload.longitude) AS h3_r9",
                "volumeos_h3_r7(payload.latitude, payload.longitude) AS h3_r7",
                "'unassigned' AS lga", "'unassigned' AS ward",
                "coalesce(payload.corridor, 'unassigned') AS corridor",
                "payload.consent_reference", "payload.lawful_basis", "payload.device_id", "month",
            )
        else:
            projections = ["tenant_id", "event_id", "source", "lineage_id", "effective_ts_unix", "record_version",
                           "year", "month", "day", "tenant_hash"]
            projections += [f"payload.{name} AS {name}" for name in fields]
            frame = frame.selectExpr(*projections)
        missing = set(self.catalog.table(silver_table).required_columns) - set(frame.columns)
        if missing:
            raise RuntimeError(f"silver transformation omitted required columns: {sorted(missing)}")
        (frame.write.format("delta").mode("append")
         .partitionBy(*self.catalog.table(silver_table).partitions)
         .save(self.path(silver_table)))

    def publish_gold(self, table: str, frame: Any, publication_id: str,
                     prior_row_count: int, duplicate_count: int = 0) -> None:
        from pyspark.sql import functions as F

        spec = self.catalog.table(table)
        if spec.zone != "gold" or not SHA256.fullmatch(publication_id):
            raise ValueError("gold target or publication manifest digest is invalid")
        if PII_COLUMNS.intersection(frame.columns):
            raise ValueError("gold batch contains prohibited PII columns")
        if table != "gt_model_registry" and "tenant_id" not in frame.columns:
            raise ValueError("gold serving batch lacks tenant_id")
        predicates = [F.col(name).isNull() | (F.trim(F.col(name).cast("string")) == "") for name in spec.required_columns]
        null_filter = predicates[0]
        for predicate in predicates[1:]:
            null_filter = null_filter | predicate
        failures = quality_decision(frame.count(), prior_row_count, duplicate_count, frame.filter(null_filter).count())
        if failures:
            raise RuntimeError("gold quality gate failed: " + "; ".join(failures))
        (frame.withColumn("publication_id", F.lit(publication_id)).write.format("delta")
         .mode("append").partitionBy(*spec.partitions).save(self.path(table)))

    def campaign_performance(self, publication_id: str, prior_row_count: int, lineage_id: str) -> None:
        from pyspark.sql import functions as F

        frame = (self.read("sl_fact_message").withColumn("event_date", F.to_date(F.from_unixtime("effective_ts_unix")))
                 .groupBy("tenant_id", "campaign_id", "event_date").agg(
                     F.sum(F.when(F.col("status") == "sent", 1).otherwise(0)).alias("sent_count"),
                     F.sum(F.when(F.col("status") == "delivered", 1).otherwise(0)).alias("delivered_count"),
                     F.sum(F.when(F.col("status") == "failed", 1).otherwise(0)).alias("failed_count"),
                 ).withColumn("source", F.lit("spark-build-marts")).withColumn("lineage_id", F.lit(lineage_id))
                 .withColumn("year", F.year("event_date")).withColumn("month", F.month("event_date"))
                 .withColumn("day", F.dayofmonth("event_date"))
                 .withColumn("tenant_hash", F.pmod(F.xxhash64("tenant_id"), F.lit(32))))
        self.publish_gold("gt_campaign_perf", frame, publication_id, prior_row_count)

    def maintenance(self, table: str) -> None:
        plan = self.catalog.maintenance_plan(table)
        optimize = plan["optimize"].replace(f"`{table}`", f"delta.`{self.path(table)}`")
        vacuum = plan["vacuum"].replace(f"`{table}`", f"delta.`{self.path(table)}`")
        self.spark.sql(optimize)
        self.spark.sql(vacuum)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--conform", nargs=2, action="append", metavar=("BRONZE", "SILVER"))
    parser.add_argument("--maintenance", action="append", default=[])
    args = parser.parse_args()
    job = SparkMedallionJob(SparkMedallionJob.create_session(), args.root)
    for bronze, silver in args.conform or []:
        job.conform(bronze, silver)
    for table in args.maintenance:
        job.maintenance(table)


if __name__ == "__main__":
    main()
