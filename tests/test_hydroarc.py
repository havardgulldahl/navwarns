#!/usr/bin/env python3
"""Tests for HYDROARC 34/26(43) multi-area parsing."""
import pytest
from scripts.parser import (
    parse_navwarns,
    parse_coordinate_groups,
    analyze_geometry,
    parse_coordinates,
)

SAMPLE_HYDROARC_34 = """HYDROARC 34/26(43).
NORWEGIAN SEA.
NORWAY.
DNC 21, DNC 22.
1. HAZARDOUS OPERATIONS, ROCKET LAUNCHING
   1930Z TO 0100Z DAILY 06 THRU 15 MAR
   IN AREAS BOUND BY:
   A. 71-01.00N 011-54.00E, 69-45.00N 014-19.00E,
      70-10.00N 016-21.00E, 70-40.00N 016-53.00E,
      71-21.00N 016-05.00E.
   B. 69-24.00N 015-47.00E, 69-16.00N 015-56.00E,
      69-17.00N 016-06.00E, 69-25.00N 016-05.00E.
2. CANCEL THIS MSG 160200Z MAR 26."""


def test_hydroarc_34_26_coordinate_groups():
    """Two coordinate groups should be detected (A and B)."""
    groups = parse_coordinate_groups(SAMPLE_HYDROARC_34)
    assert len(groups) == 2
    assert len(groups[0]) == 5  # Area A: 5 points
    assert len(groups[1]) == 4  # Area B: 4 points


def test_hydroarc_34_26_geometry_is_polygon():
    """'IN AREAS BOUND BY' should trigger polygon geometry."""
    coords = parse_coordinates(SAMPLE_HYDROARC_34)
    geom, radius = analyze_geometry(SAMPLE_HYDROARC_34, coords)
    assert geom == "polygon"
    assert radius is None


def test_hydroarc_34_26_features():
    """Multi-group message should produce two polygon features."""
    messages = parse_navwarns(SAMPLE_HYDROARC_34)
    assert len(messages) == 1
    msg = messages[0]
    assert msg.msg_id == "HYDROARC 34/26(43)"
    assert msg.hazard_type == "hazardous operations"
    assert msg.year == 2026
    assert msg.cancellations == ["THIS MSG 160200Z MAR 26"]

    features = msg.to_geojson_features()
    assert len(features) == 2, "Should produce 2 features (A and B)"

    fa = features[0]
    assert fa["properties"]["group_index"] == 1
    assert fa["geometry"]["type"] == "Polygon"
    coords_a = fa["geometry"]["coordinates"][0]
    # First point of A: 71-01.00N 011-54.00E -> lat 71.0167, lon 11.9
    assert coords_a[0][0] == pytest.approx(11.9, abs=0.01)
    assert coords_a[0][1] == pytest.approx(71.0167, abs=0.01)
    # Ring should be closed (5 points + closing = 6)
    assert len(coords_a) == 6

    fb = features[1]
    assert fb["properties"]["group_index"] == 2
    assert fb["geometry"]["type"] == "Polygon"
    coords_b = fb["geometry"]["coordinates"][0]
    # First point of B: 69-24.00N 015-47.00E -> lat 69.4, lon 15.783
    assert coords_b[0][0] == pytest.approx(15.783, abs=0.01)
    assert coords_b[0][1] == pytest.approx(69.4, abs=0.01)
    # Ring should be closed (4 points + closing = 5)
    assert len(coords_b) == 5
