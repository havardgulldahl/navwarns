import pytest
from scripts.parser import parse_navwarns

SAMPLE_TEXT = """NAVAREA XX 156/25BARENTS AND WHITE SEASAND CHYOSHSKAYA GUBA.CHART RUS 10100.1. MISSILE FIRINGS 0300 UTC TO 1700 UTC DAILY22 TO 23 NOV NAVIGATION PROHIBITED IN TERRITORIALWATERS DANGEROUS OUTSIDE IN AREAS BOUNDED BY:A. 73-48.0N 040-10.0E, 68-33.0N 044-42.0E,THEN COASTAL LINE TO 66-59.0N 044-24.0E,66-54.0N 043-31.0E, 73-33.0N 037-31.0E,B. 67-42.0N 045-18.0E, 67-07.0N 045-42.0E,67-07.0N 045-37.0E, THEN COASTAL LINE TO67-42.0N 045-18.0E.2. CANCEL THIS MSG 231800 NOV 25.=NNNN"""


def test_navarea_xx_156_25_parsing():
    messages = parse_navwarns(SAMPLE_TEXT)
    assert len(messages) == 1
    msg = messages[0]

    assert msg.msg_id == "NAVAREA XX 156/25"
    assert msg.year == 2025

    geojson_features = msg.to_geojson_features()
    assert (
        len(geojson_features) == 2
    ), "Should be split into 2 features (Area A and Area B)"

    # Check Feature 1 (Area A)
    f1 = geojson_features[0]
    assert f1["geometry"]["type"] == "Polygon"
    # Expected coordinates for Area A
    # Input: 73-48.0N 040-10.0E -> 40.1666667, 73.8
    #        68-33.0N 044-42.0E -> 44.7, 68.55
    #        66-59.0N 044-24.0E -> 44.4, 66.9833333
    #        66-54.0N 043-31.0E -> 43.5166667, 66.9
    #        73-33.0N 037-31.0E -> 37.5166667, 73.55
    #        First point repeated to close loop

    expected_coords_a = [
        [40.1666667, 73.8],
        [44.7, 68.55],
        [44.4, 66.9833333],
        [43.5166667, 66.9],
        [37.5166667, 73.55],
        [40.1666667, 73.8],  # Closed
    ]

    # Allow some floating point tolerance
    actual_coords_a = f1["geometry"]["coordinates"][0]

    assert len(actual_coords_a) == len(expected_coords_a)
    for i in range(len(expected_coords_a)):
        assert actual_coords_a[i][0] == pytest.approx(
            expected_coords_a[i][0], abs=0.001
        )
        assert actual_coords_a[i][1] == pytest.approx(
            expected_coords_a[i][1], abs=0.001
        )

    # Check Feature 2 (Area B)
    f2 = geojson_features[1]
    assert f2["geometry"]["type"] == "Polygon"
    # Expected coordinates for Area B
    # A. ... B. 67-42.0N 045-18.0E, 67-07.0N 045-42.0E,67-07.0N 045-37.0E, THEN COASTAL LINE TO67-42.0N 045-18.0E
    # B data:
    # 67-42.0N 045-18.0E -> 45.3, 67.7
    # 67-07.0N 045-42.0E -> 45.7, 67.1166667
    # 67-07.0N 045-37.0E -> 45.6166667, 67.1166667
    # Back to start: 45.3, 67.7

    expected_coords_b = [
        [45.3, 67.7],
        [45.7, 67.1166667],
        [45.6166667, 67.1166667],
        [45.3, 67.7],
    ]

    actual_coords_b = f2["geometry"]["coordinates"][0]
    assert len(actual_coords_b) == len(expected_coords_b)
    for i in range(len(expected_coords_b)):
        assert actual_coords_b[i][0] == pytest.approx(
            expected_coords_b[i][0], abs=0.001
        )
        assert actual_coords_b[i][1] == pytest.approx(
            expected_coords_b[i][1], abs=0.001
        )


SAMPLE_NAVAREA_XX_158 = "NAVAREA XX 158/25BARENTS SEA.CHART RUS 10100.1. ROCKET LAUNCHING 1300 TO 1435 UTC DAILY25 TO 29 NOV NAVIGATION PROHIBITED IN TERRITORIALWATERS DANGEROUS OUTSIDE IN AREA BOUNDED BY:A. 70-47-00N 046-22-00E, 70-37-00N 047-36-00E,69-46-00N 046-36-00E, 69-56-00N 045-20-00E.B. 74-04-00N 051-13-30E, 73-51-40N 052-40-00E,72-44-00N 050-36-00E, 72-57-00N 049-13-00E.2. CANCEL THIS MSG 291535 UTC NOV 25.=NNNN"


def test_navarea_xx_158_25_parsing():
    messages = parse_navwarns(SAMPLE_NAVAREA_XX_158)
    assert len(messages) == 1
    msg = messages[0]

    assert msg.msg_id == "NAVAREA XX 158/25"
    assert msg.year == 2025

    features = msg.to_geojson_features()
    assert len(features) == 2, "Should have 2 features (Area A and Area B)"

    # Sort by group_index
    features.sort(key=lambda x: x["properties"].get("group_index", 0))

    # Feature A
    fa = features[0]
    coords_a = fa["geometry"]["coordinates"][0]
    # Check first point: 70-47-00N 046-22-00E -> 70.783333, 46.366667
    assert coords_a[0][0] == pytest.approx(46.366667, abs=0.001)
    assert coords_a[0][1] == pytest.approx(70.783333, abs=0.001)

    # Feature B
    fb = features[1]
    coords_b = fb["geometry"]["coordinates"][0]
    # Check first point: 74-04-00N 051-13-30E -> 74.066667, 51.225
    assert coords_b[0][0] == pytest.approx(51.225, abs=0.001)
    assert coords_b[0][1] == pytest.approx(74.066667, abs=0.001)


SAMPLE_NAVAREA_XX_28_26 = "NAVAREA XX 28/26BARENTS SEA.CHART RUS 10100.1. MISSILE FIRINGS 0000 TO 1300 UTCDAILY 11 TO 13 MAR IN AREA DANGEROUSTO NAVIGATION BOUNDED BY:72-46.0N 035-00.5E, 70-28.5N 038-18.0E,69-38.0N 038-45.0E, 69-28.0N 038-00.0E,72-03.5N 031-04.0E.2. CANCEL THIS MSG 131400 UTC MAR 26.=NNNN"


def test_navarea_xx_28_26_is_polygon():
    messages = parse_navwarns(SAMPLE_NAVAREA_XX_28_26)
    assert len(messages) == 1
    msg = messages[0]

    assert msg.msg_id == "NAVAREA XX 28/26"
    assert msg.year == 2026
    assert msg.geometry == "polygon"
    assert len(msg.coordinates) == 5

    feat = msg.to_geojson_feature()
    assert feat["geometry"]["type"] == "Polygon"
    ring = feat["geometry"]["coordinates"][0]
    # Polygon ring must be closed (first == last)
    assert ring[0][0] == pytest.approx(ring[-1][0], abs=1e-6)
    assert ring[0][1] == pytest.approx(ring[-1][1], abs=1e-6)
    # 5 input points + closure = 6 ring positions
    assert len(ring) == 6
    # First point: 72-46.0N 035-00.5E -> lat 72.7667, lon 35.0083
    assert ring[0][0] == pytest.approx(35.0083, abs=0.001)
    assert ring[0][1] == pytest.approx(72.7667, abs=0.001)


SAMPLE_NAVAREA_XX_182 = """NAVAREA XX 182/25
KARA SEA.
CHART RUS 11126.
1. SPECIAL ACTIVITIES 01 JAN TO 30 JUN
NAVIGATION PROHIBITED IN TERRITORIAL
WATERS DANGEROUS OUTSIDE IN AREA BOUNDED BY:
A. 74-13.0N 058-44.0E THEN COASTAL LINE TO
74-34.0N 059-44.0E, 74-26.0N 060-37.0E, 74-04.0N 059-46.0E.
B. 73-26.0N 057-11.0E THEN COASTAL LINE TO
73-45.0N 057-50.0E, 73-37.0N 058-52.0E, 73-17.0N 058-19.0E.
C. 72-13.0N 055-34.0E THEN COASTAL LINE TO
72-40.0N 055-57.0E, 72-38.0N 057-04.0E, 72-12.0N 056-53.0E.
2. CANCEL THIS MSG 01 JUL 26.=
NNNN"""


def test_navarea_xx_182_25_parsing():
    messages = parse_navwarns(SAMPLE_NAVAREA_XX_182)
    assert len(messages) == 1
    msg = messages[0]

    assert msg.msg_id == "NAVAREA XX 182/25"
    assert msg.year == 2025

    features = msg.to_geojson_features()
    assert len(features) == 3, "Should have 3 features (A, B, C)"

    # Sort by group_index
    features.sort(key=lambda x: x["properties"].get("group_index", 0))

    # Feature A
    fa = features[0]
    coords_a = fa["geometry"]["coordinates"][0]
    # Check first point: 74-13.0N 058-44.0E -> 74.216667, 58.733333
    assert coords_a[0][0] == pytest.approx(58.733333, abs=0.001)
    assert coords_a[0][1] == pytest.approx(74.216667, abs=0.001)
    # Check last explicitly defined point before closure
    # 74-04.0N 059-46.0E -> 74.066667, 59.766667

    # Feature B
    fb = features[1]
    coords_b = fb["geometry"]["coordinates"][0]
    # Check first point: 73-26.0N 057-11.0E -> 73.433333, 57.183333
    assert coords_b[0][0] == pytest.approx(57.183333, abs=0.001)
    assert coords_b[0][1] == pytest.approx(73.433333, abs=0.001)

    # Feature C
    fc = features[2]
    coords_c = fc["geometry"]["coordinates"][0]
    # Check first point: 72-13.0N 055-34.0E -> 72.216667, 55.566667
    assert coords_c[0][0] == pytest.approx(55.566667, abs=0.001)
    assert coords_c[0][1] == pytest.approx(72.216667, abs=0.001)


# ---------------------------------------------------------------------------
# Russian Cyrillic NAVAREAXX messages (from Rosatom scraper, unknown_id.json)
# ---------------------------------------------------------------------------

# Approximation of the Russian body found in current/navwarns/unknown_id.json
# Key properties: Cyrillic msg_id, self-cancel via "ОТМ ЭТОТ НР 01 ИЮЛЬ"
SAMPLE_RUSSIAN_NAVAREA_XX_182 = (
    "НАВАРЕА 200 182/25 КАРТА 11126 КАРСКОЕ МОРЕ "
    "1. СПЕЦИАЛЬНЫЕ РАБОТЫ 01 ЯНВ ПО 30 ИЮНЯ "
    "ПЛАВАНИЕ ЗАПРЕЩЕНО ТЕРВОДАХ ОПАСНО ИХ ПРЕДЕЛАМИ РАЙОНАХ "
    "А. 74-13.0С 058-44.0В ДАЛЕЕ ПО БЕРЕГОВОЙ ЛИНИИ ДО "
    "74-34.0С 059-44.0В, 74-26.0С 060-37.0В, 74-04.0С 059-46.0В. "
    "Б. 73-26.0С 057-11.0В ДАЛЕЕ ПО БЕРЕГОВОЙ ЛИНИИ ДО "
    "73-45.0С 057-50.0В, 73-37.0С 058-52.0В, 73-17.0С 058-19.0В. "
    "В. 72-13.0С 055-34.0В ДАЛЕЕ ПО БЕРЕГОВОЙ ЛИНИИ ДО "
    "72-40.0С 055-57.0В, 72-38.0С 057-04.0В, 72-12.0С 056-53.0В. "
    "2. ОТМ ЭТОТ НР 01 ИЮЛЬ= НННН"
)


def test_russian_navarea_msg_id_pattern():
    """MSG_ID_PATTERN must match the Russian НАВАРЕА format."""
    from scripts.parser import MSG_ID_PATTERN

    m = MSG_ID_PATTERN.search(SAMPLE_RUSSIAN_NAVAREA_XX_182)
    assert m is not None, "MSG_ID_PATTERN did not match Russian НАВАРЕА ID"
    assert "182/25" in m.group(1)


def test_russian_navarea_year_extraction():
    """parse_navwarns() must set year=2025 when msg_id is НАВАРЕА 200 182/25."""
    messages = parse_navwarns(SAMPLE_RUSSIAN_NAVAREA_XX_182)
    assert len(messages) >= 1
    msg = messages[0]
    assert msg.msg_id is not None, "msg_id should be parsed from Russian НАВАРЕА ID"
    assert "182/25" in msg.msg_id
    assert msg.year == 2025


def test_russian_navarea_valid_until():
    """ОТМ ЭТОТ НР 01 ИЮЛЬ must produce valid_until = 2025-07-01."""
    messages = parse_navwarns(SAMPLE_RUSSIAN_NAVAREA_XX_182)
    assert len(messages) >= 1
    msg = messages[0]
    valid_until = msg._compute_valid_until()
    assert (
        valid_until is not None
    ), "valid_until must not be None for Russian self-cancel"
    assert valid_until.startswith(
        "2025-07-01"
    ), f"Expected 2025-07-01, got {valid_until}"
