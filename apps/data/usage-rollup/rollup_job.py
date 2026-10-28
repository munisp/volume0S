"""PyFlink exactly-once usage deduplication and daily rollup job.

Kafka record timestamps supply UTC event days. Charge and provider-cost amounts
remain separate; only explicitly rated events enter the billable rollup.
"""
from __future__ import annotations

import os

ROLLUP_SQL = """
INSERT INTO usage_rollups
SELECT tenant_id, provider, channel, campaign_id, offer,
       DATE_FORMAT(recorded_at, 'yyyy-MM-dd') AS day,
       SUM(`count`) AS event_count,
       SUM(charge_minor) AS amount_minor,
       SUM(cost_minor) AS provider_cost_minor,
       currency
FROM (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY event_id ORDER BY recorded_at DESC
    ) AS duplicate_rank
    FROM usage_events
)
WHERE duplicate_rank = 1
  AND status = 'accepted'
  AND charge_minor > 0
GROUP BY tenant_id, provider, channel, campaign_id, offer,
         DATE_FORMAT(recorded_at, 'yyyy-MM-dd'), currency
"""


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def main() -> None:
    from pyflink.datastream import CheckpointingMode, StreamExecutionEnvironment
    from pyflink.table import StreamTableEnvironment

    brokers = required("KAFKA_BOOTSTRAP_SERVERS")
    source_topic = os.environ.get("KAFKA_USAGE_TOPIC", "usage.events")
    sink_topic = os.environ.get("KAFKA_ROLLUP_TOPIC", "usage.rollups")
    group_id = os.environ.get("KAFKA_ROLLUP_GROUP", "volumeos-usage-rollup-v1")
    environment = StreamExecutionEnvironment.get_execution_environment()
    environment.enable_checkpointing(60_000, CheckpointingMode.EXACTLY_ONCE)
    table = StreamTableEnvironment.create(environment)
    table.get_config().get_configuration().set_string("table.local-time-zone", "UTC")
    table.get_config().get_configuration().set_string(
        "execution.checkpointing.unaligned.enabled", "true"
    )
    table.execute_sql(f"""
        CREATE TABLE usage_events (
            event_id STRING,
            tenant_id STRING,
            provider STRING,
            campaign_id STRING,
            offer STRING,
            channel STRING,
            `count` BIGINT,
            cost_minor BIGINT,
            charge_minor BIGINT,
            currency STRING,
            status STRING,
            recorded_at TIMESTAMP_LTZ(3) METADATA FROM 'timestamp'
        ) WITH (
            'connector' = 'kafka',
            'topic' = '{source_topic}',
            'properties.bootstrap.servers' = '{brokers}',
            'properties.group.id' = '{group_id}',
            'scan.startup.mode' = 'earliest-offset',
            'format' = 'json',
            'json.ignore-parse-errors' = 'false'
        )
    """)
    table.execute_sql(f"""
        CREATE TABLE usage_rollups (
            tenant_id STRING,
            provider STRING,
            channel STRING,
            campaign_id STRING,
            offer STRING,
            day STRING,
            event_count BIGINT,
            amount_minor BIGINT,
            provider_cost_minor BIGINT,
            currency STRING,
            PRIMARY KEY (tenant_id, provider, channel, campaign_id, offer, day, currency) NOT ENFORCED
        ) WITH (
            'connector' = 'upsert-kafka',
            'topic' = '{sink_topic}',
            'properties.bootstrap.servers' = '{brokers}',
            'properties.compression.type' = 'producer',
            'key.format' = 'json',
            'value.format' = 'json'
        )
    """)
    # Py4J exposes Java's await method; await is a Python keyword.
    getattr(table.execute_sql(ROLLUP_SQL), "await")()


if __name__ == "__main__":
    main()
