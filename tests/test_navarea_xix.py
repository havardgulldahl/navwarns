"""Tests for NAVAREA XIX scraper (Kystverket)."""

import pytest
from scripts.parser import parse_navwarns, parse_cancellations
from scripts.scraper_navareaxix import extract_warnings, normalize_dtg


# --- Sample HTML from the ASPX page (simplified) ---
SAMPLE_HTML = b"""<!DOCTYPE html>
<html>
<body>
<table id="GridView1" width="100%">
<tr><th>NAVAREA XIX Warnings</th></tr>
<tr class="Item"><td>
<table border="2"><tr><td>Number:</td><td>34/26</td></tr>
<tr><td>Date:</td><td>061830 UTC mar 26</td></tr>
<tr><td valign="top">Warning:</td><td>NAVAREA XIX 34/26<BR />
1. NAVAREA XIX WARNINGS INFORCE AT 061830 UTC MAR 26<BR />
2025 SERIES: 1<BR />
2026 SERIES: 31, 33, 34<BR />
2. CANCEL NAVAREA XIX 32/26</td></tr></table>
</td></tr>
<tr class="Item"><td>
<table border="2"><tr><td>Number:</td><td>31/26</td></tr>
<tr><td>Date:</td><td>271830 UTC feb 26</td></tr>
<tr><td valign="top">Warning:</td><td>NAVAREA XIX 31/26<BR />
<BR />
1. RIGLIST. CORRECT AT 271830 UTC FEB 26<BR />
NORWEGIAN SEA: NORTH OF 65N, EAST OF 5W.<BR />
<BR />
72-31.40N 020-20.60E TRANSOCEAN ENABLER<BR />
72-22.00N 020-07.30E COSL PROSPECTOR<BR />
65-51.70N 007-23.80E SCARABEO 8<BR />
65-31.00N 007-12.00E TRANSOCEAN NORGE<BR />
65-18.70N 007-15.70E ISLAND INNOVATOR<BR />
65-01.90N 006-52.40E TRANSOCEAN ENCOURAGE<BR />
<BR />
NOTES:<BR />
A. RIGS ARE PROTECTED BY A 500 METRE SAFETY ZONE.<BR />
2. CANCEL NAVAREA XIX 29/26</td></tr></table>
</td></tr>
<tr class="Item"><td>
<table border="2"><tr><td>Number:</td><td>1/25</td></tr>
<tr><td>Date:</td><td>160630 UTC feb 26</td></tr>
<tr><td valign="top">Warning:</td><td>NAVAREA XIX 1/25<BR />
1.THE NORWEGIAN GOVERNMENT HAS DECIDED THAT <BR />
RUSSIAN FISHING VESSELS ONLY HAVE ACCESS TO THE <BR />
FOLLOWING THREE PORTS ON THE NORWEGIAN MAINLAND:<BR />
 <BR />
A.BAATSFJORD (NOBJF)<BR />
B.KIRKENES (NOKKN)<BR />
C.TROMSOE (NOTOS)<BR />
 <BR />
A BAN ON ACCESS TO OTHER PORTS ON THE NORWEGIAN MAINLAND<BR />
WILL APPLY FOR RUSSIAN FISHING VESSELS FROM 132200 UTC OCT 2022.<BR />
2. CANCEL NAVAREA XIX 115/24</td></tr></table>
</td></tr>
</table>
</body>
</html>"""


class TestExtractWarnings:
    def test_extracts_three_warnings(self):
        warnings = extract_warnings(SAMPLE_HTML)
        assert len(warnings) == 3

    def test_first_warning_fields(self):
        warnings = extract_warnings(SAMPLE_HTML)
        w = warnings[0]
        assert w["number"] == "34/26"
        assert w["date"] == "061830 UTC mar 26"
        assert "NAVAREA XIX 34/26" in w["body"]

    def test_second_warning_has_coordinates(self):
        warnings = extract_warnings(SAMPLE_HTML)
        w = warnings[1]
        assert "72-31.40N 020-20.60E" in w["body"]
        assert w["number"] == "31/26"

    def test_third_warning_body(self):
        warnings = extract_warnings(SAMPLE_HTML)
        w = warnings[2]
        assert "RUSSIAN FISHING VESSELS" in w["body"]
        assert w["number"] == "1/25"


class TestNormalizeDtg:
    def test_standard_format(self):
        assert normalize_dtg("061830 UTC mar 26") == "061830Z MAR 26"

    def test_uppercase(self):
        assert normalize_dtg("271830 UTC FEB 26") == "271830Z FEB 26"

    def test_extra_whitespace(self):
        assert normalize_dtg("  160630 UTC feb 26  ") == "160630Z FEB 26"


class TestParseNavareaXIX:
    def test_inforce_message(self):
        dtg = normalize_dtg("061830 UTC mar 26")
        body = (
            "NAVAREA XIX 34/26\n"
            "1. NAVAREA XIX WARNINGS INFORCE AT 061830 UTC MAR 26\n"
            "2025 SERIES: 1\n"
            "2026 SERIES: 31, 33, 34\n"
            "2. CANCEL NAVAREA XIX 32/26"
        )
        msgs = parse_navwarns(f"{dtg}\n{body}")
        assert len(msgs) == 1
        msg = msgs[0]
        assert msg.msg_id == "NAVAREA XIX 34/26"
        assert msg.year == 2026
        assert msg.dtg is not None

    def test_riglist_message_coordinates(self):
        dtg = normalize_dtg("271830 UTC feb 26")
        body = (
            "NAVAREA XIX 31/26\n"
            "\n"
            "1. RIGLIST. CORRECT AT 271830 UTC FEB 26\n"
            "NORWEGIAN SEA: NORTH OF 65N, EAST OF 5W.\n"
            "\n"
            "72-31.40N 020-20.60E TRANSOCEAN ENABLER\n"
            "72-22.00N 020-07.30E COSL PROSPECTOR\n"
            "65-51.70N 007-23.80E SCARABEO 8\n"
            "65-31.00N 007-12.00E TRANSOCEAN NORGE\n"
            "65-18.70N 007-15.70E ISLAND INNOVATOR\n"
            "65-01.90N 006-52.40E TRANSOCEAN ENCOURAGE\n"
            "\n"
            "NOTES:\n"
            "A. RIGS ARE PROTECTED BY A 500 METRE SAFETY ZONE.\n"
            "2. CANCEL NAVAREA XIX 29/26"
        )
        msgs = parse_navwarns(f"{dtg}\n{body}")
        assert len(msgs) == 1
        msg = msgs[0]
        assert msg.msg_id == "NAVAREA XIX 31/26"
        assert len(msg.coordinates) == 6
        # First rig: 72-31.40N 020-20.60E -> lat 72.5233, lon 20.3433
        assert msg.coordinates[0][0] == pytest.approx(72.5233, abs=0.001)
        assert msg.coordinates[0][1] == pytest.approx(20.3433, abs=0.001)

    def test_riglist_geometry_is_multipoint(self):
        dtg = normalize_dtg("271830 UTC feb 26")
        body = (
            "NAVAREA XIX 31/26\n"
            "\n"
            "1. RIGLIST. CORRECT AT 271830 UTC FEB 26\n"
            "NORWEGIAN SEA: NORTH OF 65N, EAST OF 5W.\n"
            "\n"
            "72-31.40N 020-20.60E TRANSOCEAN ENABLER\n"
            "72-22.00N 020-07.30E COSL PROSPECTOR\n"
            "65-51.70N 007-23.80E SCARABEO 8\n"
            "65-31.00N 007-12.00E TRANSOCEAN NORGE\n"
            "65-18.70N 007-15.70E ISLAND INNOVATOR\n"
            "65-01.90N 006-52.40E TRANSOCEAN ENCOURAGE\n"
            "\n"
            "NOTES:\n"
            "A. RIGS ARE PROTECTED BY A 500 METRE SAFETY ZONE.\n"
            "B. FOR RIGS LOCATED SOUTH OF 65N, REFER TO "
            "NAVAREA I WARNINGS OR VISIT WWW.UKHO.GOV.UK/RNW\n"
            "2. CANCEL NAVAREA XIX 29/26"
        )
        msgs = parse_navwarns(f"{dtg}\n{body}")
        assert len(msgs) == 1
        msg = msgs[0]
        assert msg.geometry == "multipoint"
        feat = msg.to_geojson_feature()
        assert feat["geometry"]["type"] == "MultiPoint"
        assert len(feat["geometry"]["coordinates"]) == 6

    def test_cancellations_captured(self):
        body = (
            "NAVAREA XIX 34/26\n"
            "1. NAVAREA XIX WARNINGS INFORCE AT 061830 UTC MAR 26\n"
            "2. CANCEL NAVAREA XIX 32/26"
        )
        cancels = parse_cancellations(body)
        assert any("32/26" in c for c in cancels)

    def test_navarea_xix_cancel_full_id(self):
        body = "2. CANCEL NAVAREA XIX 32/26"
        cancels = parse_cancellations(body)
        assert "NAVAREA XIX 32/26" in cancels

    def test_port_restriction_no_coordinates(self):
        dtg = normalize_dtg("160630 UTC feb 26")
        body = (
            "NAVAREA XIX 1/25\n"
            "1.THE NORWEGIAN GOVERNMENT HAS DECIDED THAT\n"
            "RUSSIAN FISHING VESSELS ONLY HAVE ACCESS TO THE\n"
            "FOLLOWING THREE PORTS ON THE NORWEGIAN MAINLAND:\n"
            "A.BAATSFJORD (NOBJF)\n"
            "B.KIRKENES (NOKKN)\n"
            "C.TROMSOE (NOTOS)\n"
            "A BAN ON ACCESS TO OTHER PORTS ON THE NORWEGIAN MAINLAND\n"
            "WILL APPLY FOR RUSSIAN FISHING VESSELS FROM 132200 UTC OCT 2022.\n"
            "2. CANCEL NAVAREA XIX 115/24"
        )
        msgs = parse_navwarns(f"{dtg}\n{body}")
        assert len(msgs) == 1
        msg = msgs[0]
        assert msg.msg_id == "NAVAREA XIX 1/25"
        assert msg.year == 2025
        assert len(msg.coordinates) == 0

    def test_geojson_output(self):
        dtg = normalize_dtg("271830 UTC feb 26")
        body = (
            "NAVAREA XIX 31/26\n"
            "72-31.40N 020-20.60E TRANSOCEAN ENABLER\n"
            "72-22.00N 020-07.30E COSL PROSPECTOR\n"
        )
        msgs = parse_navwarns(f"{dtg}\n{body}")
        assert len(msgs) == 1
        feat = msgs[0].to_geojson_feature()
        assert feat["type"] == "Feature"
        assert feat["id"] == "NAVAREA XIX 31/26"
        assert feat["geometry"] is not None
        assert feat["properties"]["msg_id"] == "NAVAREA XIX 31/26"
        assert feat["properties"]["year"] == 2026
