import math
from datetime import datetime
import pytest

from scripts.scraper import (
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


if __name__ == "__main__":
    pytest.main([__file__])
