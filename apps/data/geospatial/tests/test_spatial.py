from datetime import date

import h3
from shapely.geometry import Point, Polygon, mapping

from spatial import (
    Boundary, H3SedonaIndexer, digitally_silent, lga_density, route_targets, territory_features,
)


def boundary():
    return Boundary("Lagos Mainland", "Yaba", "yaba", Polygon([(3.30, 6.45), (3.45, 6.45), (3.45, 6.60), (3.30, 6.60)]))


def poi(**changes):
    value = {
        "tenant_id": "tenant-a", "poi_id": "poi-1", "latitude": 6.5095, "longitude": 3.3711,
        "rating": 4.5, "last_post_date": "2026-07-01", "email_campaigns": False,
        "social_active": False, "niche": "pharmacy", "scoring_eligible": True,
    }
    index = H3SedonaIndexer([boundary()]).index(value["latitude"], value["longitude"])
    value.update(index)
    value.update(changes)
    return value


def test_h3_indices_and_spatial_join_use_the_required_resolutions():
    index = H3SedonaIndexer([boundary()]).index(6.5095, 3.3711)
    assert h3.get_resolution(index["h3_r9"]) == 9
    assert h3.get_resolution(index["h3_r7"]) == 7
    assert index["lga"] == "Lagos Mainland" and index["ward"] == "Yaba"
    assert index["geometry"].startswith("POINT")


def test_digital_silence_requires_dormancy_no_email_and_a_winable_rating():
    assert digitally_silent({"last_post_date": "2026-08-29", "email_campaigns": False, "rating": 4.0}, date(2026, 10, 28))
    assert not digitally_silent({"last_post_date": "2026-08-30", "email_campaigns": False, "rating": 4.5}, date(2026, 10, 28))
    assert not digitally_silent({"last_post_date": None, "email_campaigns": True, "rating": 4.5}, date(2026, 10, 28))
    assert not digitally_silent({"last_post_date": None, "email_campaigns": False, "rating": 3.9}, date(2026, 10, 28))


def test_territory_features_are_explainable_tenant_scoped_and_exclude_low_confidence():
    low_confidence = poi(poi_id="poi-low", scoring_eligible=False)
    features = territory_features("tenant-a", [poi(), low_confidence], date(2026, 10, 28), "spark-sedona", "run-7")
    assert len(features) == 1
    row = features[0]
    assert row["observation_count"] == 1 and row["density"] == 1
    assert row["digital_silence"] == 1 and row["value"] == 0.9 and row["competition"] == 0
    assert row["geometry"].startswith("POLYGON")
    density = lga_density("tenant-a", [poi(), low_confidence], "spark-sedona", "run-7")
    assert len(density) == 1 and density[0]["poi_count"] == 1


def test_cross_tenant_pois_cannot_leak_into_a_feature_build():
    try:
        territory_features("tenant-a", [poi(tenant_id="tenant-b")], date(2026, 10, 28), "spark", "run")
        assert False, "cross-tenant feature input was accepted"
    except ValueError:
        pass


class Routes:
    def isochrone(self, latitude, longitude, minutes):
        return {
            "type": "Feature", "properties": {},
            "geometry": mapping(Polygon([(3.30, 6.45), (3.45, 6.45), (3.45, 6.60), (3.30, 6.60)])),
        }


def test_isochrone_routes_validate_geometry_and_filter_census_targets():
    targets = [
        {"poi_id": "inside", "latitude": 6.51, "longitude": 3.37},
        {"poi_id": "outside", "latitude": 6.8, "longitude": 3.1},
    ]
    selected = route_targets(Routes(), 6.5, 3.37, 10, targets)
    assert [target["poi_id"] for target in selected] == ["inside"]
    assert selected[0]["isochrone_minutes"] == 10
    try:
        route_targets(Routes(), 6.5, 3.37, 20, targets)
        assert False, "unreviewed isochrone interval was accepted"
    except ValueError:
        pass
