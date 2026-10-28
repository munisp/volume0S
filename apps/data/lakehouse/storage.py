"""Append-only Parquet/Delta storage boundaries; no bronze update API exists."""
from __future__ import annotations

from typing import Any, Protocol

from catalog import Catalog


class BronzeStore(Protocol):
    def append(self, table: str, row: dict[str, Any]) -> None: ...


class DeltaBronzeStore:
    """Production S3/MinIO adapter. Credentials stay in the supplied SDK options."""

    def __init__(self, root_uri: str, storage_options: dict[str, str], catalog: Catalog | None = None):
        if not root_uri.startswith(("s3://", "s3a://")):
            raise ValueError("bronze root must be an S3-compatible URI")
        self.root_uri = root_uri.rstrip("/")
        self.storage_options = storage_options
        self.catalog = catalog or Catalog.load()

    def append(self, table: str, row: dict[str, Any]) -> None:
        spec = self.catalog.table(table)
        if spec.zone != "bronze":
            raise ValueError("DeltaBronzeStore accepts bronze tables only")
        missing = set(spec.required_columns) - set(row)
        if missing:
            raise ValueError(f"bronze evidence is incomplete: {sorted(missing)}")
        import pyarrow as pa
        from deltalake import write_deltalake

        values = {key: value for key, value in row.items() if key != "table"}
        arrow = pa.Table.from_pylist([values])
        write_deltalake(
            f"{self.root_uri}/{table}",
            arrow,
            mode="append",
            partition_by=list(spec.partitions),
            storage_options=self.storage_options,
        )


class InMemoryBronzeStore:
    """Test double only; it is not a production lakehouse or durability fallback."""

    def __init__(self):
        self.rows: list[tuple[str, dict[str, Any]]] = []

    def append(self, table: str, row: dict[str, Any]) -> None:
        self.rows.append((table, dict(row)))
