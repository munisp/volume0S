"""Versioned ingestion envelopes and canonical bronze evidence rows."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

ULID = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SCHEMA = re.compile(r"^volumeos\.[a-z0-9_]+\.v[1-9][0-9]*$")


class ContractError(ValueError):
    """Malformed input must be quarantined, never silently discarded."""


@dataclass(frozen=True)
class EventEnvelope:
    event_id: str
    tenant_id: str
    source: str
    schema: str
    ts_unix: int
    lineage_id: str

    def __post_init__(self) -> None:
        if not ULID.fullmatch(self.event_id):
            raise ContractError("event_id must be a canonical uppercase ULID")
        for name in ("tenant_id", "source", "lineage_id"):
            if not IDENTIFIER.fullmatch(getattr(self, name)):
                raise ContractError(f"{name} is not a safe bounded identifier")
        if not SCHEMA.fullmatch(self.schema):
            raise ContractError("schema must be a versioned VolumeOS schema")
        if not 0 <= self.ts_unix <= 4_102_444_800:
            raise ContractError("ts_unix is outside the supported event-time range")

    @classmethod
    def from_dict(cls, value: Any) -> "EventEnvelope":
        if not isinstance(value, dict):
            raise ContractError("envelope must be an object")
        try:
            return cls(
                event_id=str(value["event_id"]),
                tenant_id=str(value["tenant_id"]),
                source=str(value["source"]),
                schema=str(value["schema"]),
                ts_unix=int(value["ts_unix"]),
                lineage_id=str(value["lineage_id"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError("envelope fields are missing or invalid") from exc

    def bronze_row(self, table: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ContractError("event payload must be an object")
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        event_date = datetime.fromtimestamp(self.ts_unix, timezone.utc)
        corridor = payload.get("corridor") if isinstance(payload.get("corridor"), str) else "unassigned"
        return {
            "table": table,
            "event_id": self.event_id,
            "tenant_id": self.tenant_id,
            "source": self.source,
            "schema": self.schema,
            "ts_unix": self.ts_unix,
            "lineage_id": self.lineage_id,
            "payload_json": payload_json,
            "payload_sha256": hashlib.sha256(payload_json.encode()).hexdigest(),
            "year": event_date.year,
            "month": event_date.month,
            "day": event_date.day,
            "tenant_hash": int.from_bytes(hashlib.sha256(self.tenant_id.encode()).digest()[:2], "big") % 32,
            "corridor": corridor,
        }
