"""Gold marts publish only as complete, quality-approved tenant-scoped versions."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

from catalog import Catalog
from quality import QualityPolicy, QualityReport, evaluate_quality

PII_COLUMNS = {"phone", "phone_number", "msisdn", "email", "address", "contact_name"}


class GoldStore(Protocol):
    def publish(self, table: str, publication_id: str, rows: list[dict[str, Any]]) -> None: ...


class DeltaGoldStore:
    """Append complete publication versions; DataFusion selects an approved version."""

    def __init__(self, root_uri: str, storage_options: dict[str, str], catalog: Catalog | None = None):
        if not root_uri.startswith(("s3://", "s3a://")):
            raise ValueError("gold root must be an S3-compatible URI")
        self.root_uri = root_uri.rstrip("/")
        self.storage_options = storage_options
        self.catalog = catalog or Catalog.load()

    def publish(self, table: str, publication_id: str, rows: list[dict[str, Any]]) -> None:
        spec = self.catalog.table(table)
        if spec.zone != "gold":
            raise ValueError("DeltaGoldStore accepts gold tables only")
        import pyarrow as pa
        from deltalake import write_deltalake

        arrow = pa.Table.from_pylist([{**row, "publication_id": publication_id} for row in rows])
        write_deltalake(
            f"{self.root_uri}/{table}", arrow, mode="append",
            partition_by=list(spec.partitions), storage_options=self.storage_options,
        )


class GoldPublisher:
    def __init__(self, store: GoldStore, catalog: Catalog | None = None,
                 policy: QualityPolicy = QualityPolicy()):
        self.store = store
        self.catalog = catalog or Catalog.load()
        self.policy = policy

    def publish(self, table: str, rows: list[dict[str, Any]], prior_row_count: int,
                duplicate_count: int = 0) -> QualityReport:
        report = evaluate_quality(table, rows, prior_row_count, duplicate_count, self.policy, self.catalog)
        if not report.passed:
            return report
        for row in rows:
            if PII_COLUMNS.intersection(row):
                return QualityReport(False, ("gold contains prohibited person-identifiable columns",), report.row_count_delta, report.dedup_rate)
            if self.catalog.table(table).name != "gt_model_registry" and not row.get("tenant_id"):
                return QualityReport(False, ("gold serving rows must carry tenant_id",), report.row_count_delta, report.dedup_rate)
        canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str).encode()
        publication_id = hashlib.sha256(table.encode() + b"\0" + canonical).hexdigest()
        self.store.publish(table, publication_id, rows)
        return report


class InMemoryGoldStore:
    """Test double; published versions are replaced only after all gates pass."""

    def __init__(self):
        self.versions: dict[str, list[tuple[str, list[dict[str, Any]]]]] = {}

    def publish(self, table: str, publication_id: str, rows: list[dict[str, Any]]) -> None:
        self.versions.setdefault(table, []).append((publication_id, [dict(row) for row in rows]))
