"""Authoritative Delta table catalog and Spark maintenance plans."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TableSpec:
    name: str
    zone: str
    owner: str
    cadence: str
    partitions: tuple[str, ...]
    retention: str
    required_columns: tuple[str, ...]


class Catalog:
    def __init__(self, tables: list[TableSpec]):
        self.tables = tuple(tables)
        names = [table.name for table in tables]
        if not tables or len(names) != len(set(names)):
            raise ValueError("catalog tables must be present and unique")
        for table in tables:
            if table.zone not in {"bronze", "silver", "gold"}:
                raise ValueError(f"unknown lakehouse zone: {table.zone}")
            if not table.owner or not table.required_columns:
                raise ValueError(f"table {table.name} lacks an owner or schema")

    @classmethod
    def load(cls, path: Path | None = None) -> "Catalog":
        source = path or Path(__file__).with_name("catalog.json")
        document = json.loads(source.read_text())
        if document.get("version") != 1 or not isinstance(document.get("tables"), list):
            raise ValueError("unsupported lakehouse catalog")
        return cls([
            TableSpec(
                name=row["name"], zone=row["zone"], owner=row["owner"], cadence=row["cadence"],
                partitions=tuple(row["partitions"]), retention=row["retention"],
                required_columns=tuple(row["required_columns"]),
            ) for row in document["tables"]
        ])

    def table(self, name: str) -> TableSpec:
        for table in self.tables:
            if table.name == name:
                return table
            if table.name.endswith("*") and name.startswith(table.name[:-1]) and len(name) > len(table.name) - 1:
                return table
        raise KeyError(f"table is not in the M3 catalog: {name}")

    def maintenance_plan(self, name: str) -> dict[str, object]:
        table = self.table(name)
        spatial = name in {"br_census_pings", "sl_dim_poi", "gt_territory_scores", "gt_lga_density"}
        zorder = ("corridor", "lga") if spatial else ("tenant_id", "event_date")
        vacuum = "720 hours" if table.zone != "gold" else "17520 hours"
        return {
            "table": name,
            "optimize": f'OPTIMIZE `{name}` ZORDER BY ({", ".join(zorder)})',
            "vacuum": f'VACUUM `{name}` RETAIN {vacuum}',
            "cadence": "nightly optimize; weekly vacuum",
        }
