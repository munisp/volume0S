import json

import pytest

from catalog import Catalog
from jobs.spark_etl import SparkMedallionJob, TRANSFORMS, quality_decision


def test_spark_payload_allowlists_never_include_raw_contact_or_address_fields():
    for fields in TRANSFORMS.values():
        assert not {"phone", "phone_number", "msisdn", "email", "address", "contact_name"}.intersection(fields)
    assert TRANSFORMS[("br_census_pings", "sl_dim_poi")]["phone_hash"] == "string"


def test_spark_covers_every_direct_fact_and_census_conformance_contract():
    expected = {
        ("br_events_message", "sl_fact_message"), ("br_events_usage", "sl_fact_usage"),
        ("br_cdc_payments", "sl_fact_payment"), ("br_cdc_ai_jobs", "sl_fact_ai_job"),
        ("br_cdc_tenants", "sl_dim_tenant"), ("br_census_pings", "sl_dim_poi"),
    }
    assert set(TRANSFORMS) == expected
    catalog = Catalog.load()
    for bronze, silver in expected:
        assert catalog.table(bronze).zone == "bronze"
        assert catalog.table(silver).zone == "silver"


def test_gold_quality_decision_blocks_empty_drift_duplicates_and_null_keys():
    assert quality_decision(0, 0, 0, 0)
    assert quality_decision(10, 100, 0, 0)
    assert quality_decision(100, 100, 3, 0)
    assert quality_decision(100, 100, 0, 1)
    assert quality_decision(100, 100, 0, 0) == ()


def test_spark_job_requires_s3_and_approved_maintenance_tables():
    with pytest.raises(ValueError):
        SparkMedallionJob(object(), "file:///tmp/lake")
    job = SparkMedallionJob(object(), "s3://volumeos-lake")
    with pytest.raises(KeyError):
        job.maintenance("unapproved_table")
    plan = Catalog.load().maintenance_plan("br_census_pings")
    assert "ZORDER BY (corridor, lga)" in plan["optimize"]
    assert "RETAIN 720 hours" in plan["vacuum"]
    gold = Catalog.load().maintenance_plan("gt_campaign_perf")
    assert "RETAIN 17520 hours" in gold["vacuum"]


def test_catalog_covers_all_medallion_tables_and_engine_owners():
    catalog = Catalog.load()
    names = {table.name for table in catalog.tables}
    expected = {
        "br_events_usage", "br_events_message", "br_census_pings", "br_scrape_runs", "br_cdc_*",
        "sl_dim_tenant", "sl_dim_lead", "sl_dim_poi", "sl_fact_message", "sl_fact_usage",
        "sl_fact_payment", "sl_fact_ai_job", "gt_campaign_perf", "gt_territory_scores", "gt_lga_density",
        "gt_client_health", "gt_cost_per_client", "gt_revenue_by_offer", "gt_model_registry",
    }
    assert names == expected
    for table in catalog.tables:
        assert table.owner and table.required_columns and table.partitions
