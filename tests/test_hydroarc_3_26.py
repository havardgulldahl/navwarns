import pytest
from scripts.parser import parse_navwarns

SAMPLE_HYDROARC = """HYDROARC 3/26(GEN).
NORWEGIAN SEA.
NORTH PACIFIC.
NORWAY.
1. HAZARDOUS OPERATIONS, ROCKET LAUNCHING
   2005Z TO 2020Z DAILY 19 THRU 25 JAN
   IN AREAS BOUND BY:
   A. 27-28.71N 171-24.34W, 13-38.98N 174-00.45W,
      01-35.98N 175-48.45W, 00-54.72N 172-43.89W,
      12-22.59N 170-40.14W, 16-45.76N 170-06.75W,
      26-43.75N 168-39.09W.
   B. 68-59.33N 015-28.05E, 69-13.37N 015-58.52E,
      70-53.83N 014-10.90E, 70-41.62N 012-51.94E.
   C. 71-03.42N 015-12.97E, 74-15.83N 009-52.26E,
      76-50.36N 003-23.81E, 79-11.76N 006-04.25W,
      79-55.85N 010-21.89W, 79-41.45N 011-34.90W,
      78-03.47N 004-13.39W, 76-01.02N 002-00.03E,
      73-32.12N 007-12.24E, 70-30.12N 011-37.50E.
2. CANCEL THIS MSG 252120Z JAN 26."""


def test_hydroarc_3_26_parsing():
    messages = parse_navwarns(SAMPLE_HYDROARC)
    assert len(messages) == 1
    msg = messages[0]

    assert "HYDROARC 3/26(GEN)" in msg.msg_id
    # Year inference: 26 -> 2026
    assert msg.year == 2026

    features = msg.to_geojson_features()
    assert len(features) == 3, "Should have 3 features (A, B, C)"

    # Sort features by group index just to be sure, though list order should be preserved
    # The parser assigns properties.group_index
    features.sort(key=lambda x: x["properties"].get("group_index", 0))

    # Feature A
    fa = features[0]
    # Check simple properties
    # assert fa["properties"]["area_id"] == "A"  <-- Not currently supported by parser, it uses group_index=1
    assert fa["properties"]["group_index"] == 1

    coords_a = fa["geometry"]["coordinates"][0]
    # First point: 27-28.71N 171-24.34W -> ~27.4785, -171.405667
    assert coords_a[0][0] == pytest.approx(-171.405667, abs=0.001)
    assert coords_a[0][1] == pytest.approx(27.478500, abs=0.001)

    # Feature B
    fb = features[1]
    assert fb["properties"]["group_index"] == 2
    coords_b = fb["geometry"]["coordinates"][0]
    # First point: 68-59.33N 015-28.05E -> ~68.988833, 15.4675
    assert coords_b[0][0] == pytest.approx(15.4675, abs=0.001)
    assert coords_b[0][1] == pytest.approx(68.988833, abs=0.001)

    # Feature C
    fc = features[2]
    assert fc["properties"]["group_index"] == 3
    coords_c = fc["geometry"]["coordinates"][0]
    # First point: 71-03.42N 015-12.97E -> ~71.057, 15.216167
    assert coords_c[0][0] == pytest.approx(15.216167, abs=0.001)
    assert coords_c[0][1] == pytest.approx(71.057, abs=0.001)


SAMPLE_HYDROARC_206 = """HYDROARC 206/25(43).
NORWEGIAN SEA.
NORWAY.
DNC 21, DNC 22.

    HAZARDOUS OPERATIONS, ROCKET LAUNCHING
    0600Z TO 1100Z DAILY 13 THRU 27 NOV
    IN AREAS BOUND BY:
    A. 74-35.00N 016-04.00E, 74-19.00N 011-35.00E,
    73-25.00N 012-47.00E, 73-40.00N 016-50.00E.
    B. 70-29.00N 016-24.00E, 71-06.00N 014-37.00E,
    70-31.00N 012-39.00E, 69-53.00N 014-33.00E.
    C. 69-48.00N 016-09.00E, 69-47.00N 015-33.00E,
    69-35.00N 015-38.00E, 69-36.00N 016-12.00E.
    D. 69-26.30N 016-10.50E, 69-28.60N 015-57.40E,
    69-27.20N 015-49.40E, 69-21.10N 015-34.50E,
    69-16.60N 016-04.10E.
    CANCEL THIS MSG 271200Z NOV 25."""


def test_hydroarc_206_25_parsing():
    messages = parse_navwarns(SAMPLE_HYDROARC_206)
    assert len(messages) == 1
    msg = messages[0]

    # Check ID and year
    assert "HYDROARC 206/25" in msg.msg_id
    assert msg.year == 2025

    # Check features count
    features = msg.to_geojson_features()
    assert len(features) == 4, "Should have 4 features (A, B, C, D)"

    # Sort by group_index
    features.sort(key=lambda x: x["properties"].get("group_index", 0))

    # --- Feature A ---
    fa = features[0]
    coords_a = fa["geometry"]["coordinates"][0]
    # Check first coord: 74-35.00N 016-04.00E -> 16.066667, 74.583333
    assert coords_a[0][0] == pytest.approx(16.066667, abs=0.001)
    assert coords_a[0][1] == pytest.approx(74.583333, abs=0.001)

    # --- Feature B ---
    fb = features[1]
    coords_b = fb["geometry"]["coordinates"][0]
    # Check first coord: 70-29.00N 016-24.00E -> 16.4, 70.483333
    assert coords_b[0][0] == pytest.approx(16.4, abs=0.001)
    assert coords_b[0][1] == pytest.approx(70.483333, abs=0.001)

    # --- Feature C ---
    fc = features[2]
    coords_c = fc["geometry"]["coordinates"][0]
    # Check first coord: 69-48.00N 016-09.00E -> 16.15, 69.8
    assert coords_c[0][0] == pytest.approx(16.15, abs=0.001)
    assert coords_c[0][1] == pytest.approx(69.8, abs=0.001)

    # --- Feature D ---
    fd = features[3]
    coords_d = fd["geometry"]["coordinates"][0]
    # Check first coord: 69-26.30N 016-10.50E -> 16.175, 69.438333
    assert coords_d[0][0] == pytest.approx(16.175, abs=0.001)
    assert coords_d[0][1] == pytest.approx(69.438333, abs=0.001)
