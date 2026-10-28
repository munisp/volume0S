"""Immutable Delta-shaped model registry with signed human review before CRM sync."""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Protocol


def canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class RegistryStore(Protocol):
    def get(self, model_id: str, version: str) -> dict[str, Any] | None: ...
    def put_if_absent(self, record: dict[str, Any]) -> None: ...


class ModelRegistry:
    def __init__(self, store: RegistryStore, review_key: bytes):
        if len(review_key) < 32:
            raise ValueError("model review key must contain at least 32 bytes")
        self.store = store
        self.review_key = review_key

    @staticmethod
    def record_digest(record: dict[str, Any]) -> str:
        content = {key: value for key, value in record.items() if key not in {"review_state", "review"}}
        return hashlib.sha256(canonical(content)).hexdigest()

    def propose(self, model_id: str, version: str, weight_version: str,
                params: dict[str, Any], metrics: dict[str, Any], lineage_id: str) -> dict[str, Any]:
        if not all((model_id, version, weight_version, lineage_id)) or not params or not metrics:
            raise ValueError("model identity, parameters, metrics, and lineage are required")
        record = {
            "model_id": model_id, "model_version": version, "weight_version": weight_version,
            "params_json": json.dumps(params, sort_keys=True), "metrics_json": json.dumps(metrics, sort_keys=True),
            "review_state": "pending", "source": "ray", "lineage_id": lineage_id,
        }
        self.store.put_if_absent(record)
        return record

    def sign_review(self, record: dict[str, Any], reviewer: str, decision: str) -> str:
        if decision not in {"approved", "rejected"} or not reviewer:
            raise ValueError("review requires a reviewer and approved/rejected decision")
        payload = {
            "model_id": record["model_id"], "model_version": record["model_version"],
            "weight_version": record["weight_version"], "record_sha256": self.record_digest(record),
            "reviewer": reviewer, "decision": decision,
        }
        return hmac.new(self.review_key, canonical(payload), hashlib.sha256).hexdigest()

    def review(self, model_id: str, version: str, reviewer: str, decision: str, signature: str) -> dict[str, Any]:
        record = self.store.get(model_id, version)
        if record is None or record["review_state"] != "pending":
            raise ValueError("model version is absent or already reviewed")
        expected = self.sign_review(record, reviewer, decision)
        if not hmac.compare_digest(expected, signature):
            raise ValueError("invalid human review signature")
        # Versioned Delta stores retain the proposal and append this review decision;
        # the serving pointer changes only after the signature has been verified.
        reviewed = {**record, "review_state": decision, "review": {
            "reviewer": reviewer, "signature": signature, "record_sha256": self.record_digest(record),
        }}
        self.store.put_if_absent(reviewed)
        return reviewed

    def crm_sync(self, model_id: str, version: str, weight_version: str,
                 scores: list[dict[str, Any]]) -> list[dict[str, Any]]:
        record = self.store.get(model_id, version)
        if (record is None or record["review_state"] != "approved"
                or record["weight_version"] != weight_version):
            raise ValueError("CRM sync requires approval of the exact model and weight versions")
        for score in scores:
            if score.get("model_version") != version or score.get("weight_version") != weight_version:
                raise ValueError("stale or mixed model output cannot enter CRM")
        return [{**score, "review_record_sha256": record["review"]["record_sha256"]} for score in scores]


class InMemoryRegistryStore:
    """Append-only test double; production uses the gt_model_registry Delta table."""

    def __init__(self):
        self.records: dict[tuple[str, str, str], dict[str, Any]] = {}

    def get(self, model_id: str, version: str) -> dict[str, Any] | None:
        values = [record for (mid, ver, state), record in self.records.items()
                  if mid == model_id and ver == version]
        if not values:
            return None
        return max(values, key=lambda record: record["review_state"] != "pending")

    def put_if_absent(self, record: dict[str, Any]) -> None:
        key = (record["model_id"], record["model_version"], record["review_state"])
        if key in self.records and self.records[key] != record:
            raise ValueError("immutable model registry record changed")
        self.records[key] = dict(record)
