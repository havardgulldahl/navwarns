"""Regression test for messages containing multiple named areas (AREA 1, AREA 2, …).

Each named area is bounded by labelled vertices (A., B., C., D.) which must be
collected into a single polygon per area, not split into individual points.

Test input:  tests/test_data/test_message_XIX_rocket_debris.txt
Expected:    tests/test_data/test_message_XIX_rocket_debris_expected.geojson
"""

import json
import math
import pathlib

import pytest

from scripts.parser import NavwarnMessage, parse_coordinate_groups

DATA_DIR = pathlib.Path(__file__).parent / "test_data"
MESSAGE_FILE = DATA_DIR / "test_message_XIX_rocket_debris.txt"
EXPECTED_FILE = DATA_DIR / "test_message_XIX_rocket_debris_expected.geojson"


@pytest.fixture(scope="module")
def message_body() -> str:
    return MESSAGE_FILE.read_text()


@pytest.fixture(scope="module")
def expected_geojson() -> dict:
    return json.loads(EXPECTED_FILE.read_text())


@pytest.fixture(scope="module")
def parsed_message(message_body) -> NavwarnMessage:
    return NavwarnMessage.from_text("", message_body)


# ---------------------------------------------------------------------------
# Group-level tests
# ---------------------------------------------------------------------------


def test_two_coordinate_groups_are_produced(message_body):
    """AREA 1 and AREA 2 must each form one group, not eight single-point groups."""
    groups = parse_coordinate_groups(message_body)
    assert len(groups) == 2, f"Expected 2 groups, got {len(groups)}: {groups}"


def test_each_group_has_four_vertices(message_body):
    """Each AREA block contains four labelled vertices (A–D)."""
    groups = parse_coordinate_groups(message_body)
    for i, group in enumerate(groups):
        assert (
            len(group) == 4
        ), f"Group {i} should have 4 vertices, got {len(group)}: {group}"


# ---------------------------------------------------------------------------
# Feature-level tests
# ---------------------------------------------------------------------------


def test_two_polygon_features_produced(parsed_message):
    """Output must be two GeoJSON features, one per area."""
    features = parsed_message.to_geojson_features()
    assert len(features) == 2, f"Expected 2 features, got {len(features)}"


def test_features_are_polygons(parsed_message):
    """Both features must have Polygon geometry, not Point."""
    for i, feat in enumerate(parsed_message.to_geojson_features()):
        geom = feat["geometry"]
        assert geom is not None, f"Feature {i} has null geometry"
        assert (
            geom["type"] == "Polygon"
        ), f"Feature {i} geometry should be Polygon, got {geom['type']}"


def test_polygons_are_closed(parsed_message):
    """GeoJSON polygon rings must start and end with the same coordinate."""
    for i, feat in enumerate(parsed_message.to_geojson_features()):
        ring = feat["geometry"]["coordinates"][0]
        assert (
            ring[0] == ring[-1]
        ), f"Feature {i} ring is not closed: first={ring[0]}, last={ring[-1]}"


# ---------------------------------------------------------------------------
# Coordinate accuracy tests (compared against expected GeoJSON)
# ---------------------------------------------------------------------------


def _coords_close(actual, expected, tol=1e-4) -> bool:
    """Return True when two [lon, lat] pairs are within *tol* degrees."""
    return math.isclose(actual[0], expected[0], abs_tol=tol) and math.isclose(
        actual[1], expected[1], abs_tol=tol
    )


def test_polygon_coordinates_match_expected(parsed_message, expected_geojson):
    """Vertex coordinates must match the expected GeoJSON within 1e-4 degrees."""
    actual_features = parsed_message.to_geojson_features()
    expected_features = expected_geojson["features"]

    assert len(actual_features) == len(
        expected_features
    ), f"Feature count mismatch: {len(actual_features)} != {len(expected_features)}"

    for feat_idx, (actual_feat, expected_feat) in enumerate(
        zip(actual_features, expected_features)
    ):
        actual_ring = actual_feat["geometry"]["coordinates"][0]
        expected_ring = expected_feat["geometry"]["coordinates"][0]

        # Compare all vertices (including the closing repeated point)
        assert len(actual_ring) == len(
            expected_ring
        ), f"Feature {feat_idx}: ring length {len(actual_ring)} != {len(expected_ring)}"

        for v_idx, (actual_v, expected_v) in enumerate(zip(actual_ring, expected_ring)):
            assert _coords_close(actual_v, expected_v), (
                f"Feature {feat_idx}, vertex {v_idx}: "
                f"got {actual_v}, expected {expected_v}"
            )
