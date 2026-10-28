from __future__ import annotations

import json

import pytest

from catalog import Catalog
from conform import SilverConformer
from contracts import ContractError, EventEnvelope
from gold import GoldPublisher, InMemoryGoldStore
from ingest import BronzeIngester
from quality import evaluate_quality
from storage import InMemoryBronzeStore

EVENT_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def envelope() -> EventEnvelope:
    return EventEnvelope(EVENT_ID, "tenant-a", "census-sync", "volumeos.census.v1", 1_760_000_000, "run-7")


def raw_event() -> bytes:
    return json.dumps({
        "envelope": {
            "event_id": EVENT_ID, "tenant_id": "tenant-a", "source": "census-sync",
            "schema": "volumeos.census.v1", "ts_unix": 1_760_000_000, "lineage_id": "run-7",
        },
        "payload": {"corridor": "yaba", "poi_id": "poi-1"},
    }).encode()


class Dedup:
    def __init__(self):
        self.ids = set()

    def contains(self, consumer, event_id):
        return (consumer, event_id) in self.ids

    def remember(self, consumer, event_id):
        self.ids.add((consumer, event_id))


class DLQ(list):
    def publish(self, topic, record):
        self.append((topic, record))


class Spatial:
    def index(self, latitude, longitude):
        return {
            "h3_r9": "891f0000000ffff", "h3_r7": "871f00000ffffff",
            "geometry": f"POINT({longitude} {latitude})", "lga": "Lagos Mainland",
            "ward": "Yaba", "corridor": "yaba",
        }


def test_catalog_covers_every_normative_table_and_maintenance_boundary():
    catalog = Catalog.load()
    for name in (
        "br_events_usage", "br_events_message", "br_census_pings", "br_scrape_runs",
        "sl_dim_tenant", "sl_dim_lead", "sl_dim_poi", "sl_fact_message", "sl_fact_usage",
        "sl_fact_payment", "sl_fact_ai_job", "gt_campaign_perf", "gt_territory_scores",
        "gt_lga_density", "gt_client_health", "gt_cost_per_client", "gt_revenue_by_offer",
        "gt_model_registry",
    ):
        assert catalog.table(name).required_columns
    assert catalog.table("br_cdc_tenancy").name == "br_cdc_*"
    assert "720 hours" in catalog.maintenance_plan("sl_fact_usage")["vacuum"]
    assert "17520 hours" in catalog.maintenance_plan("gt_campaign_perf")["vacuum"]
    assert "corridor" in catalog.maintenance_plan("gt_lga_density")["optimize"]


def test_ingestion_is_append_only_replay_safe_and_never_silently_drops_bad_input():
    store, dedup, dlq = InMemoryBronzeStore(), Dedup(), DLQ()
    ingester = BronzeIngester("bronze-census", store, dedup, dlq)
    assert ingester.consume("br_census_pings", raw_event()).state == "appended"
    assert ingester.consume("br_census_pings", raw_event()).state == "duplicate"
    assert len(store.rows) == 1
    table, row = store.rows[0]
    assert table == "br_census_pings"
    assert row["event_id"] == EVENT_ID and row["corridor"] == "yaba"
    assert len(row["payload_sha256"]) == 64
    assert ingester.consume("br_census_pings", b"not-json").state == "quarantined"
    assert dlq[0][0] == "br_census_pings_dlq"
    assert "raw" not in dlq[0][1] and len(dlq[0][1]["raw_sha256"]) == 64


def test_envelope_rejects_non_ulid_and_unversioned_schema():
    with pytest.raises(ContractError):
        EventEnvelope("event-1", "tenant-a", "source", "volumeos.census.v1", 1, "run")
    with pytest.raises(ContractError):
        EventEnvelope(EVENT_ID, "tenant-a", "source", "census", 1, "run")


def test_silver_conformance_masks_pii_geocodes_and_deduplicates():
    conformer = SilverConformer("silver-poi", Dedup(), b"m" * 32, spatial=Spatial())
    payload = {
        "poi_id": "poi-1", "latitude": 6.5095, "longitude": 3.3711,
        "phone": "0803 123 4567", "notes": "Call 08031234567 after 9am",
    }
    row = conformer.conform_poi(envelope(), payload)
    assert row["phone_hash"] == conformer.phone_hash("+2348031234567")
    assert "0803" not in json.dumps(row)
    assert row["notes"] == "Call [phone] after 9am"
    assert row["h3_r9"] and row["h3_r7"] and row["scoring_eligible"] is True
    assert conformer.conform_poi(envelope(), payload) is None
    assert len(conformer.phone_hash("08031234567")) == 64


def test_low_confidence_geocoding_is_retained_but_excluded_from_scoring():
    from conform import GeoResult

    class LowGeocoder:
        def geocode(self, address):
            return GeoResult(6.5, 3.3, "fallback", 0.2)

    conformer = SilverConformer("silver-poi", Dedup(), b"m" * 32, geocoder=LowGeocoder(), spatial=Spatial())
    row = conformer.conform_poi(envelope(), {"poi_id": "poi-low", "address": "Yaba, Lagos"})
    assert row["geo_source"] == "fallback"
    assert row["geo_confidence"] == 0.2
    assert row["scoring_eligible"] is False


def gold_row():
    return {
        "tenant_id": "tenant-a", "campaign_id": "campaign-1", "event_date": "2026-10-28",
        "sent_count": 10, "delivered_count": 9, "failed_count": 1,
        "source": "spark", "lineage_id": "run-7",
    }


def test_failed_quality_gate_publishes_nothing_and_two_percent_dedup_fails():
    store = InMemoryGoldStore()
    publisher = GoldPublisher(store)
    row = gold_row()
    row["campaign_id"] = None
    report = publisher.publish("gt_campaign_perf", [row], 1)
    assert not report.passed and not store.versions
    report = evaluate_quality("gt_campaign_perf", [gold_row()] * 98, 98, duplicate_count=2)
    assert not report.passed and any("dedup rate" in failure for failure in report.failures)


def test_quality_approved_gold_is_a_complete_immutable_version_without_raw_pii():
    store = InMemoryGoldStore()
    publisher = GoldPublisher(store)
    report = publisher.publish("gt_campaign_perf", [gold_row()], 1)
    assert report.passed
    assert len(store.versions["gt_campaign_perf"]) == 1
    changed = gold_row()
    changed["phone"] = "08031234567"
    report = publisher.publish("gt_campaign_perf", [changed], 1)
    assert not report.passed
    assert len(store.versions["gt_campaign_perf"]) == 1
