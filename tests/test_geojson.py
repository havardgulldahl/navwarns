import pytest
from scripts.parser import NavwarnMessage
from datetime import datetime


def build_msg(geometry, coords, radius=None):
    return NavwarnMessage(
        dtg=None,
        raw_dtg="RAW",
        msg_id="TEST/00",
        coordinates=coords,
        cancellations=[],
        hazard_type="general",
        geometry=geometry,
        radius=radius,
        body="BODY",
    )


def test_point_geojson():
    m = build_msg("point", [(10.0, 20.0)])
    g = m.geojson_geometry()
    assert g["type"] == "Point"
    assert g["coordinates"] == [20.0, 10.0]


def test_linestring_geojson():
    m = build_msg("linestring", [(10.0, 20.0), (11.0, 21.0), (12.0, 22.0)])
    g = m.geojson_geometry()
    assert g["type"] == "LineString"
    assert g["coordinates"][0] == [20.0, 10.0]
    assert g["coordinates"][2] == [22.0, 12.0]


def test_polygon_geojson():
    m = build_msg("polygon", [(10.0, 20.0), (10.5, 20.5), (10.0, 21.0)])
    g = m.geojson_geometry()
    assert g["type"] == "Polygon"
    ring = g["coordinates"][0]
    assert ring[0] == [20.0, 10.0]
    assert ring[-1] == ring[0]  # closed
    assert len(ring) == 4  # 3 points + closure


def test_circle_geojson_segments():
    m = build_msg("circle", [(60.0, -45.0)], radius=5)  # 5 NM
    g = m.geojson_geometry(circle_segments=36)
    assert g["type"] == "Polygon"
    ring = g["coordinates"][0]
    assert len(ring) == 37  # 36 + closure
    # All points roughly 5NM in degrees from center
    lats = [pt[1] for pt in ring[:-1]]
    lons = [pt[0] for pt in ring[:-1]]
    assert max(abs(lat - 60.0) for lat in lats) > 0  # actually offset
    # Center not duplicated inside ring
    assert [-45.0, 60.0] not in ring


def test_feature_wrapper():
    m = build_msg("point", [(1.0, 2.0)])
    f = m.to_geojson_feature()
    assert f["type"] == "Feature"
    assert f["geometry"]["type"] == "Point"
    assert f["properties"]["msg_id"] == "TEST/00"
