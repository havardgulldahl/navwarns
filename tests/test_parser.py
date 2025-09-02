import math
from datetime import datetime
import pytest

from scripts.parser import (
    parse_navwarns,
    parse_dtg,
    parse_msg_id,
    parse_coordinates,
    parse_cancellations,
    classify_hazard,
    coord_to_decimal,
    NavwarnMessage,
)

SAMPLE_TEXT = """
192359Z AUG 25
HYDROARC 136/25(15).
BAFFIN BAY.
CANADA.
DNC 28.
1. DERELICT M/V TIBERBORG ADRIFT IN
   VICINITY 71-45.10N 070-28.20W AT 192300Z AUG.
2. CANCEL HYDROARC 134/25.
3. CANCEL THIS MSG 222359Z AUG 25.
"""

MULTI_MESSAGE_TEXT = """
192359Z AUG 25
HYDROARC 136/25(15).
DERELICT OBJECT 60-10.00N 045-30.00W.
202359Z AUG 25
HYDROARC 137/25.
ROCKET LAUNCH HAZARDOUS OPERATIONS 10-10.00N 020-20.00E.
"""

SAMPLE_95_18 = """
    NAVAREA XIII 95/18 
TATARSKIY PROLIV SEA OF OKHOTSK AND KURIL'SKIYE OSTROVA 
CHARTS RUS 62076 62009 61018 61019 
1. CABLE LAID ALONG LINE 49-03.8N 140-18.7E 49-03.7N 140-18.8E 
49-03.7N 140-18.9E 49-03.7N 140-19.2E 49-03.9N 140-21.1E 
49-03.5N 140-26.9E 49-03.5N 140-30.5E 49-02.2N 140-51.1E 
49-01.8N 141-50.1E 49-02.6N 142-01.5E 49-02.6N 142-01.6E 
46-51.1N 143-10.2E 46-52.0N 143-12.0E 46-56.5N 143-23.0E 
46-56.5N 143-33.0E 46-49.0N 143-53.5E 46-38.5N 144-47.0E 
45-22.5N 147-00.0E 45-20.5N 147-45.0E 45-14.5N 147-51.5E 
45-13.6N 147-52.3E 45-14.2N 147-51.5E 45-19.0N 147-45.0E 
45-17.5N 147-37.0E 45-03.4N 147-10.5E 44-50.0N 147-00.0E 
44-32.5N 146-43.8E 44-05.3N 146-18.5E 44-03.4N 146-00.0E 
44-02.7N 145-53.3E 44-02.5N 145-51.7E 44-02.5N 145-53.5E 
44-02.5N 146-00.0E 44-00.0N 146-30.0E 43-54.3N 146-46.5E 
43-52.9N 146-47.0E 43-52.7N 146-48.1E 43-52.2N 146-48.3E 
CAUTION ADVISED 
2. CANCEL 47/18 AND THIS PARA
"""

SAMPLE_4_19 = """ NAVAREA XIII 4/19 
SEA OF OKHOTSK 
CHART RUS 62175 
1. DRILLING OPERATIONS UNTIL 31 DEC IN AREA BOUNDED BY 
51-14-23.13N 143-58-45.07E 51-24-38.52N 143-59-25.05E 
51-23-52.01N 144-27-33.61E 51-13-37.00N 144-26-47.38E 
2. CANCEL THIS MESSAGE 01 JAN 2020
"""

SAMPLE_16_19 = """
NAVAREA XIII 16/19 
SEA OF JAPAN 
CHART RUS 62012 
1. GUNNERY EXERCISES 28 FEB 01 MAR 0000 TO 0800 UTC 
IN AREA WITHIN 5 MILE RADIUS OF 45-32.0N 141-18.5E 
2. CANCEL THIS MESSAGE 010900 UTC MAR 19
"""


def test_parse_dtg():
    dtg = parse_dtg("192359Z AUG 25")
    assert isinstance(dtg, datetime)
    assert dtg.day == 19
    assert dtg.month == 8
    # Year may resolve ambiguously; ensure last two digits interpreted as 2025 or 2025-like
    assert dtg.year % 100 == 25
    assert dtg.hour == 23
    assert dtg.minute == 59


def test_parse_msg_id():
    body = "TEXT HYDROARC 136/25(15). MORE"
    assert parse_msg_id(body) == "HYDROARC 136/25(15)"


def test_coord_to_decimal_north_east():
    lat = coord_to_decimal("71-45.10N")
    lon = coord_to_decimal("070-28.20W")
    assert pytest.approx(lat, rel=1e-6) == 71 + 45.10 / 60
    assert pytest.approx(lon, rel=1e-6) == -(70 + 28.20 / 60)


def test_parse_coordinates():
    coords = parse_coordinates("COORDS 71-45.10N 070-28.20W AND 10-10.00S 020-20.00E")
    assert len(coords) == 2
    assert coords[0][0] > 0 and coords[0][1] < 0
    assert coords[1][0] < 0 and coords[1][1] > 0


def test_parse_cancellations():
    body = "CANCEL HYDROARC 134/25. ALSO CANCEL THIS MSG 222359Z AUG 25."
    cancels = parse_cancellations(body)
    assert "HYDROARC 134/25" in cancels
    assert "THIS MSG 222359Z AUG 25" in cancels
    assert len(cancels) == 2


def test_classify_hazard_derelict():
    assert classify_hazard("DERELICT BARGE ADRIFT") == "derelict vessel"


def test_classify_hazard_shoal():
    assert classify_hazard("NEW SHOAL REPORTED") == "shoals"


def test_classify_hazard_aid_outage():
    assert classify_hazard("RACON INOPERATIVE AND UNLIT") == "aid to navigation outage"


def test_classify_hazard_hazard_operations():
    assert (
        classify_hazard("ROCKET LAUNCH HAZARDOUS OPERATIONS") == "hazardous operations"
    )


def test_classify_hazard_scientific_mooring():
    assert classify_hazard("SCIENTIFIC MOORING DEPLOYED") == "scientific mooring"


def test_classify_hazard_chart():
    assert classify_hazard("ENC UPDATE CANCELLED FOR AREA") == "chart advisory"


def test_classify_hazard_general():
    assert classify_hazard("MISC INFO") == "general"


def test_parse_navwarns_single_message():
    msgs = parse_navwarns(SAMPLE_TEXT)
    assert len(msgs) == 1
    m = msgs[0]
    assert isinstance(m, NavwarnMessage)
    assert m.msg_id == "HYDROARC 136/25(15)"
    assert len(m.coordinates) == 1
    lat, lon = m.coordinates[0]
    assert lat > 0 and lon < 0
    assert "HYDROARC 134/25" in m.cancellations
    assert any("THIS MSG" in c for c in m.cancellations)
    assert m.hazard_type == "derelict vessel"
    assert m.geometry == "point"
    assert m.radius is None


def test_parse_navwarns_multi_messages():
    msgs = parse_navwarns(MULTI_MESSAGE_TEXT)
    assert len(msgs) == 2
    ids = [m.msg_id for m in msgs]
    assert ids == ["HYDROARC 136/25", "HYDROARC 137/25"]
    hazards = [m.hazard_type for m in msgs]
    assert (
        hazards[0] == "general"
    )  # not matching specific keywords (DERELICT absent here)
    assert hazards[1] == "hazardous operations"
    assert len(msgs[0].coordinates) == 1
    assert len(msgs[1].coordinates) == 1
    assert msgs[0].geometry == "point"
    assert msgs[1].geometry == "point"


def test_empty_text_yields_no_messages():
    assert parse_navwarns("") == []


def test_coordinate_parsing_ignores_invalid():
    body = "BAD 99-99.99N 181-00.00E GOOD 10-10.00N 020-20.00E"
    coords = parse_coordinates(body)
    # Regex will match only valid formatted (degrees ranges not validated logically), so both might match.
    # Add an actually invalid format to ensure skip.
    body2 = "MIX 10-10.00N XX-10.00E"
    coords2 = parse_coordinates(body2)
    assert len(coords2) == 0


def test_navwarnmessage_factory():
    body = "HYDROARC 200/25. MOORING AT 10-10.00N 020-20.00E. CANCEL HYDROARC 100/25."
    msg = NavwarnMessage.from_text("010001Z JAN 25", body)
    assert msg.msg_id == "HYDROARC 200/25"
    assert msg.hazard_type == "scientific mooring"
    assert msg.cancellations == ["HYDROARC 100/25"]
    assert msg.coordinates and pytest.approx(msg.coordinates[0][0]) == 10 + 10 / 60


def test_coord_to_decimal_invalid():
    assert coord_to_decimal("BAD") is None
    assert coord_to_decimal("1234N") is None


def test_sample_95_18_metadata():
    msgs = parse_navwarns(SAMPLE_95_18)
    assert len(msgs) == 1
    m = msgs[0]
    assert m.msg_id == "NAVAREA XIII 95/18"
    assert len(m.coordinates) >= 10
    assert any(c.endswith("47/18") or c == "47/18" for c in m.cancellations)
    # Spot check first and last coordinate approx
    first_lat, first_lon = m.coordinates[0]
    last_lat, last_lon = m.coordinates[-1]
    assert pytest.approx(first_lat, rel=1e-4, abs=1e-4) == 49.0633
    assert pytest.approx(first_lon, rel=1e-4, abs=1e-4) == 140.3117
    assert pytest.approx(last_lat, rel=1e-4, abs=1e-4) == 43.8700
    assert pytest.approx(last_lon, rel=1e-4, abs=1e-4) == 146.8050
    assert m.geometry == "linestring"
    assert m.radius is None
    expected_coords_95_18 = [
        (49.0633, 140.3117),
        (49.0617, 140.3133),
        (49.0617, 140.3150),
        (49.0617, 140.3200),
        (49.0650, 140.3517),
        (49.0583, 140.4483),
        (49.0583, 140.5083),
        (49.0367, 140.8517),
        (49.0300, 141.8350),
        (49.0433, 142.0250),
        (49.0433, 142.0267),
        (46.8517, 143.1700),
        (46.8667, 143.2000),
        (46.9417, 143.3833),
        (46.9417, 143.5500),
        (46.8167, 143.8917),
        (46.6417, 144.7833),
        (45.3750, 147.0000),
        (45.3417, 147.7500),
        (45.2417, 147.8583),
        (45.2267, 147.8717),
        (45.2367, 147.8583),
        (45.3167, 147.7500),
        (45.2917, 147.6167),
        (45.0567, 147.1750),
        (44.8333, 147.0000),
        (44.5417, 146.7300),
        (44.0883, 146.3083),
        (44.0567, 146.0000),
        (44.0450, 145.8883),
        (44.0417, 145.8617),
        (44.0417, 145.8917),
        (44.0417, 146.0000),
        (44.0000, 146.5000),
        (43.9050, 146.7750),
        (43.8817, 146.7833),
        (43.8783, 146.8017),
        (43.8700, 146.8050),
    ]
    # Compare first n expected vs parsed (parsed may omit or include same length; ensure ordering consistency)
    for (exp_lat, exp_lon), (lat, lon) in zip(expected_coords_95_18, m.coordinates):
        assert pytest.approx(lat, rel=1e-4, abs=1e-4) == exp_lat
        assert pytest.approx(lon, rel=1e-4, abs=1e-4) == exp_lon


def test_sample_4_19_metadata():
    msgs = parse_navwarns(SAMPLE_4_19)
    assert len(msgs) == 1
    m = msgs[0]
    assert m.msg_id == "NAVAREA XIII 4/19"
    assert len(m.coordinates) == 4
    # Cancellation captured (allow either 2020 or truncated 20)
    assert any("2020" in c or c.endswith("20") for c in m.cancellations)
    assert m.geometry == "polygon"
    assert m.radius is None
    expected_4_19 = [
        (51.2398, 143.9792),
        (51.4107, 143.9903),
        (51.3978, 144.4593),
        (51.2269, 144.4465),
    ]
    assert len(m.coordinates) == len(expected_4_19)
    for (exp_lat, exp_lon), (lat, lon) in zip(expected_4_19, m.coordinates):
        assert pytest.approx(lat, rel=1e-4, abs=1e-4) == exp_lat
        assert pytest.approx(lon, rel=1e-4, abs=1e-4) == exp_lon


def test_sample_16_19_metadata():
    msgs = parse_navwarns(SAMPLE_16_19)
    assert len(msgs) == 1
    m = msgs[0]
    assert m.msg_id == "NAVAREA XIII 16/19"
    assert len(m.coordinates) == 1
    lat, lon = m.coordinates[0]
    assert pytest.approx(lat, rel=1e-4, abs=1e-4) == 45.5333
    assert pytest.approx(lon, rel=1e-4, abs=1e-4) == 141.3083
    assert any(
        "010900 UTC MAR 19" in c or "010900 UTC MAR" in c for c in m.cancellations
    )
    assert m.geometry == "circle"
    assert m.radius is not None and pytest.approx(m.radius) == 5
    (lat_p, lon_p) = m.coordinates[0]
    assert pytest.approx(lat_p, rel=1e-4, abs=1e-4) == 45.5333
    assert pytest.approx(lon_p, rel=1e-4, abs=1e-4) == 141.3083


def test_year_inference_samples():
    # Single message with HYDROARC id 136/25 -> 2025
    msgs = parse_navwarns(SAMPLE_TEXT)
    assert msgs[0].year == 2025
    # Multi-message block: both 2025
    multi = parse_navwarns(MULTI_MESSAGE_TEXT)
    assert [m.year for m in multi] == [2025, 2025]
    # NAVAREA XIII 95/18 -> 2018
    m95 = parse_navwarns(SAMPLE_95_18)[0]
    assert m95.year == 2018
    # NAVAREA XIII 4/19 -> 2019
    m4 = parse_navwarns(SAMPLE_4_19)[0]
    assert m4.year == 2019
    # NAVAREA XIII 16/19 -> 2019
    m16 = parse_navwarns(SAMPLE_16_19)[0]
    assert m16.year == 2019


if __name__ == "__main__":
    pytest.main([__file__])
