"""Spark-callable forward conformance: dedup, keyed PII masking, and geocoding."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from contracts import ContractError, EventEnvelope
from ingest import DedupStore

PHONE = re.compile(r"(?<!\d)(?:\+?234[ -]?[789][01][ -]?\d{4}[ -]?\d{4}|0[789][01][ -]?\d{4}[ -]?\d{4})(?!\d)")


class Geocoder(Protocol):
    def geocode(self, address: str) -> "GeoResult": ...


class SpatialIndexer(Protocol):
    def index(self, latitude: float, longitude: float) -> dict[str, Any]: ...


@dataclass(frozen=True)
class GeoResult:
    latitude: float
    longitude: float
    source: str
    confidence: float

    def __post_init__(self) -> None:
        if not -90 <= self.latitude <= 90 or not -180 <= self.longitude <= 180:
            raise ContractError("coordinates are outside valid bounds")
        if not 0 <= self.confidence <= 1 or not self.source:
            raise ContractError("geocode provenance or confidence is invalid")


class SilverConformer:
    def __init__(self, consumer: str, dedup: DedupStore, masking_key: bytes,
                 geocoder: Geocoder | None = None, spatial: SpatialIndexer | None = None):
        if len(masking_key) < 32:
            raise ValueError("a tenant-aware masking key of at least 32 bytes is required")
        self.consumer = consumer
        self.dedup = dedup
        self.masking_key = masking_key
        self.geocoder = geocoder
        self.spatial = spatial

    def phone_hash(self, value: str) -> str:
        digits = re.sub(r"\D", "", value)
        if digits.startswith("234"):
            normalized = "+" + digits
        elif digits.startswith("0") and len(digits) == 11:
            normalized = "+234" + digits[1:]
        else:
            raise ContractError("phone is not a supported Nigerian MSISDN")
        if not re.fullmatch(r"\+234[789][01]\d{8}", normalized):
            raise ContractError("phone is not a supported Nigerian MSISDN")
        return hmac.new(self.masking_key, normalized.encode(), hashlib.sha256).hexdigest()

    def _base(self, envelope: EventEnvelope) -> dict[str, Any] | None:
        if self.dedup.contains(self.consumer, envelope.event_id):
            return None
        return {
            "event_id": envelope.event_id,
            "tenant_id": envelope.tenant_id,
            "source": envelope.source,
            "lineage_id": envelope.lineage_id,
            "effective_ts_unix": envelope.ts_unix,
            "record_version": 1,
        }

    def _finish(self, envelope: EventEnvelope, row: dict[str, Any]) -> dict[str, Any]:
        self.dedup.remember(self.consumer, envelope.event_id)
        return row

    def conform_fact(self, table: str, envelope: EventEnvelope, payload: dict[str, Any]) -> dict[str, Any] | None:
        row = self._base(envelope)
        if row is None:
            return None
        for key, value in payload.items():
            if key in {"phone", "phone_number", "msisdn"}:
                row["phone_hash"] = self.phone_hash(str(value))
            elif isinstance(value, str):
                row[key] = PHONE.sub("[phone]", value)
            elif isinstance(value, (int, float, bool)) or value is None:
                row[key] = value
            else:
                row[key] = json.dumps(value, sort_keys=True, separators=(",", ":"))
        row["table"] = table
        return self._finish(envelope, row)

    def _geo(self, payload: dict[str, Any]) -> GeoResult:
        if payload.get("latitude") is not None and payload.get("longitude") is not None:
            return GeoResult(float(payload["latitude"]), float(payload["longitude"]), "provided", 1.0)
        if self.geocoder is None or not str(payload.get("address", "")).strip():
            raise ContractError("POI requires coordinates or a geocodable address")
        return self.geocoder.geocode(str(payload["address"]))

    def conform_poi(self, envelope: EventEnvelope, payload: dict[str, Any]) -> dict[str, Any] | None:
        row = self._base(envelope)
        if row is None:
            return None
        if self.spatial is None:
            raise ContractError("POI conformance requires the Sedona/H3 spatial indexer")
        geo = self._geo(payload)
        spatial = self.spatial.index(geo.latitude, geo.longitude)
        for key in ("h3_r9", "h3_r7", "geometry"):
            if not spatial.get(key):
                raise ContractError(f"spatial index omitted {key}")
        row.update({
            "table": "sl_dim_poi",
            "poi_id": str(payload["poi_id"]),
            "latitude": geo.latitude,
            "longitude": geo.longitude,
            "geo_source": geo.source,
            "geo_confidence": geo.confidence,
            "scoring_eligible": geo.confidence >= 0.8,
            **spatial,
        })
        if payload.get("phone"):
            row["phone_hash"] = self.phone_hash(str(payload["phone"]))
        row["notes"] = PHONE.sub("[phone]", str(payload.get("notes", "")))
        return self._finish(envelope, row)
