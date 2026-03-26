# test_kaliningrad_coords.py
# Run with: pytest -q test_kaliningrad_coords.py

import math

import scripts.parser as navparser  # type: ignore


ORIGINAL_NAVWARN = {
    "type": "Feature",
    "id": "KALININGRAD NAV WARN 029/26",
    "geometry": None,
    "properties": {
        "dtg": "2026-02-26T09:59:00",
        "raw_dtg": "260959Z FEB 26",
        "msg_id": "KALININGRAD NAV WARN 029/26",
        "year": 2026,
        "cancellations": [],
        "hazard_type": "general",
        "geometry_kind": "point",
        "radius_nm": None,
        "body": "[South-eastern Baltic] KALININGRAD NAV WARN 029/26\n"
        "SOUTHEASTERN BALTIC\n"
        "SHIPS EXERCISES 282100 UTC FEB THRU 312100 UTC MAR\n"
        "IN AREA TEMPORARILY DANGEROUS TO SHIPPING BR-117\n"
        "55-54N 019-03E 55-30N 020-15E\n"
        "55-04N 020-15E 54-57.50N 020-06E\n"
        "54-57.50N 019-55E 54-50N 019-50E\n"
        "54-50N 019-25E\n"
        "CAN THIS MSG 312200 UTC MAR",
    },
}


EXPECTED = {
    "msg_id": "KALININGRAD NAV WARN 029/26",
    "year": 2026,
    "hazard_type": "general",
    "geometry_kind": "linestring",
    "coordinates": [
        (55.9, 19.05),
        (55.5, 20.25),
        (55.0666666667, 20.25),
        (54.9583333333, 20.1),
        (54.9583333333, 19.9166666667),
        (54.8333333333, 19.8333333333),
        (54.8333333333, 19.4166666667),
    ],
}


def assert_coord_lists_close(actual, expected, tol=1e-6):
    assert len(actual) == len(
        expected
    ), f"len mismatch: {len(actual)} != {len(expected)}"
    for i, ((alat, alon), (elat, elon)) in enumerate(zip(actual, expected), start=1):
        assert math.isclose(alat, elat, abs_tol=tol), f"lat[{i}] {alat} != {elat}"
        assert math.isclose(alon, elon, abs_tol=tol), f"lon[{i}] {alon} != {elon}"


def test_coord_to_decimal_supports_integer_and_decimal_minutes():
    # These are the key formats in the failing NAVWARN
    assert navparser.coord_to_decimal("55-54N") == 55.9
    assert navparser.coord_to_decimal("019-03E") == 19.05
    assert math.isclose(
        navparser.coord_to_decimal("54-57.50N"), 54.9583333333, abs_tol=1e-9
    )


def test_parse_kaliningrad_navwarn_after_fix():
    body = ORIGINAL_NAVWARN["properties"]["body"]
    raw_dtg = ORIGINAL_NAVWARN["properties"]["raw_dtg"]

    msg = navparser.NavwarnMessage.from_text(raw_dtg, body)

    assert msg.msg_id == EXPECTED["msg_id"]
    assert msg.year == EXPECTED["year"]
    assert msg.hazard_type == EXPECTED["hazard_type"]
    assert msg.geometry == EXPECTED["geometry_kind"]

    assert_coord_lists_close(msg.coordinates, EXPECTED["coordinates"])

    # GeoJSON should no longer be null once coordinates parse
    geom = msg.geojson_geometry()
    assert geom is not None
    assert geom["type"] == "LineString"
    assert len(geom["coordinates"]) == len(EXPECTED["coordinates"])
