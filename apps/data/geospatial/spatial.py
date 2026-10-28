"""Deterministic geospatial core for Spark/Sedona jobs and route-sheet builders."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

import h3
from shapely.geometry import Point, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.wkt import dumps

NEIGHBORHOOD_RESOLUTION = 9
CORRIDOR_RESOLUTION = 7


@dataclass(frozen=True)
class Boundary:
    lga: str
    ward: str
    corridor: str
    geometry: BaseGeometry


class H3SedonaIndexer:
    """Point-in-polygon adapter; production boundaries are loaded by Sedona."""

    def __init__(self, boundaries: list[Boundary]):
        self.boundaries = boundaries

    def index(self, latitude: float, longitude: float) -> dict[str, Any]:
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("invalid POI coordinates")
        point = Point(longitude, latitude)
        matches = [boundary for boundary in self.boundaries if boundary.geometry.covers(point)]
        if len(matches) > 1:
            raise ValueError("POI matched overlapping authoritative boundaries")
        boundary = matches[0] if matches else None
        return {
            "h3_r9": h3.latlng_to_cell(latitude, longitude, NEIGHBORHOOD_RESOLUTION),
            "h3_r7": h3.latlng_to_cell(latitude, longitude, CORRIDOR_RESOLUTION),
            "geometry": dumps(point, rounding_precision=7),
            "lga": boundary.lga if boundary else "unassigned",
            "ward": boundary.ward if boundary else "unassigned",
            "corridor": boundary.corridor if boundary else "unassigned",
        }


def cell_geometry(cell: str) -> str:
    boundary = h3.cell_to_boundary(cell)
    coordinates = [(longitude, latitude) for latitude, longitude in boundary]
    return dumps(Polygon(coordinates), rounding_precision=7)


def digitally_silent(poi: dict[str, Any], as_of: date) -> bool:
    last_post = poi.get("last_post_date")
    if last_post is None:
        dormant = True
    else:
        last = date.fromisoformat(last_post) if isinstance(last_post, str) else last_post
        dormant = (as_of - last).days >= 60
    return dormant and not bool(poi.get("email_campaigns")) and float(poi.get("rating", 0)) >= 4.0


def territory_features(tenant_id: str, pois: list[dict[str, Any]], as_of: date,
                       source: str, lineage_id: str) -> list[dict[str, Any]]:
    """Build one explainable feature row per eligible H3-r9 cell."""
    cells: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for poi in pois:
        if poi.get("tenant_id") != tenant_id:
            raise ValueError("cross-tenant POI reached a territory build")
        if not poi.get("scoring_eligible", False):
            continue
        cell = str(poi.get("h3_r9", ""))
        if not cell or h3.get_resolution(cell) != NEIGHBORHOOD_RESOLUTION:
            raise ValueError("eligible POI lacks an H3 resolution-9 cell")
        if not poi.get("h3_r7") or h3.get_resolution(poi["h3_r7"]) != CORRIDOR_RESOLUTION:
            raise ValueError("eligible POI lacks an H3 resolution-7 corridor cell")
        cells[cell].append(poi)
    features = []
    for cell, members in sorted(cells.items()):
        silence = sum(digitally_silent(member, as_of) for member in members) / len(members)
        value = sum(min(5.0, max(0.0, float(member.get("rating", 0)))) / 5 for member in members) / len(members)
        competition = sum(bool(member.get("social_active")) for member in members) / len(members)
        first = members[0]
        features.append({
            "tenant_id": tenant_id,
            "h3_r9": cell,
            "h3_r7": first["h3_r7"],
            "geometry": cell_geometry(cell),
            "lga": first.get("lga", "unassigned"),
            "ward": first.get("ward", "unassigned"),
            "corridor": first.get("corridor", "unassigned"),
            "observation_count": len(members),
            "density": float(len(members)),
            "digital_silence": silence,
            "value": value,
            "competition": competition,
            "source": source,
            "lineage_id": lineage_id,
        })
    return features


def lga_density(tenant_id: str, pois: list[dict[str, Any]], source: str, lineage_id: str) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], int] = defaultdict(int)
    geometries: dict[tuple[str, str, str, str], str] = {}
    for poi in pois:
        if poi.get("tenant_id") != tenant_id or not poi.get("scoring_eligible", False):
            continue
        key = (poi["lga"], poi["ward"], str(poi.get("niche", "unknown")), poi["h3_r9"])
        groups[key] += 1
        geometries[key] = cell_geometry(poi["h3_r9"])
    return [{
        "tenant_id": tenant_id, "lga": key[0], "ward": key[1], "niche": key[2], "h3_r9": key[3],
        "poi_count": count, "geometry": geometries[key], "source": source, "lineage_id": lineage_id,
    } for key, count in sorted(groups.items())]


class RoutingEngine(Protocol):
    def isochrone(self, latitude: float, longitude: float, minutes: int) -> dict[str, Any]: ...


def route_targets(engine: RoutingEngine, latitude: float, longitude: float, minutes: int,
                  targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if minutes not in {5, 10, 15}:
        raise ValueError("isochrones are limited to reviewed 5/10/15-minute route sheets")
    document = engine.isochrone(latitude, longitude, minutes)
    if document.get("type") != "Feature" or document.get("geometry", {}).get("type") not in {"Polygon", "MultiPolygon"}:
        raise ValueError("routing engine returned an invalid isochrone")
    polygon = shape(document["geometry"])
    if polygon.is_empty or not polygon.is_valid:
        raise ValueError("routing engine returned empty or invalid geometry")
    selected = []
    for target in targets:
        point = Point(float(target["longitude"]), float(target["latitude"]))
        if polygon.covers(point):
            selected.append({**target, "isochrone_minutes": minutes, "isochrone_geojson": document})
    return selected
