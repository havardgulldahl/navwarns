"""Tests for PRIP header parsing and related PRIP functionality.

Covers the parse_prip_header() function with all known header variants
(Murmansk, Arkhangelsk, West), the prip_from_text() factory, parse_prips()
end-to-end, and regression tests for the Arkhangelsk parsing bug where all
headers returned (None, None, None, None, None) due to a too-strict regex.
"""

import pytest

from scripts.parser import (
    NavwarnMessage,
    parse_prip_header,
    parse_prips,
    prip_parse_cancellations,
)


# ── Sample PRIP headers collected from live mapm.ru pages ──────────────


# Murmansk: standard long form with chart and area details
MURMANSK_HEADER_DETAILS = "ПРИП МУРМАНСК 87/26 КАРТА 12000 МОТОВСКИЙ ЗАЛИВ"
# Murmansk: chart only, no area details
MURMANSK_HEADER_NO_DETAILS = "ПРИП МУРМАНСК 90/26 КАРТА 15005"
# Murmansk: multiple charts + книга
MURMANSK_HEADER_MULTI_CHARTS = "ПРИП МУРМАНСК 290/25 КАРТЫ 15005 15006 КНИГА 2103"

# Arkhangelsk: short form — chart number, no trailing text
ARKHANGELSK_HEADER_SHORT = "ПРИП АРХАНГЕЛЬСК 11/26 Карта 16007"
# Arkhangelsk: no space between keyword and chart number
ARKHANGELSK_HEADER_NOSPACE = "ПРИП АРХАНГЕЛЬСК 6/26 карта16022"
# Arkhangelsk: uses книга instead of карта
ARKHANGELSK_HEADER_KNIGA = "ПРИП АРХАНГЕЛЬСК 3/26 книга 3030"
# Arkhangelsk: uses книга with mixed case
ARKHANGELSK_HEADER_KNIGA_UC = "ПРИП АРХАНГЕЛЬСК 5/26 КНИГА 3031"

# West: standard form
WEST_HEADER = "ПРИП ЗАПАД 42/26 КАРТА 11000 БАРЕНЦЕВО МОРЕ"

# Invalid: missing region
INVALID_HEADER_NO_REGION = "ПРИП 42/26 КАРТА 11000"
# Invalid: not a PRIP line at all
INVALID_HEADER_GARBAGE = "SOME RANDOM TEXT"


# ── Sample PRIP full texts ─────────────────────────────────────────────


ARKHANGELSK_PRIP_FULL = """\
ПРИП АРХАНГЕЛЬСК 11/26 Карта 16007
БЕЛОЕ МОРЕ
ПЕРЕДАВАТЬ 9 СУТОК
ПРИП АРХАНГЕЛЬСК 11 Карта 16007
БЕЛОЕ МОРЕ
СПЕЦИАЛЬНЫЕ РАБОТЫ 10 ПО 12 ИЮЛ 0700 ДО 1900
РАЙОНЕ ЗАПРЕТНОМ ДЛЯ ПЛАВАНИЯ
64-37.0С 038-37.0В
64-41.0С 038-37.0В
64-41.0С 038-56.0В
64-37.0С 038-56.0В="""

MURMANSK_PRIP_FULL = """\
ПРИП МУРМАНСК 87/26 КАРТА 12000 МОТОВСКИЙ ЗАЛИВ
СТВОР ЗНАКОВ ТАРИМБЕРКА
69-22.6С 033-43.1В НР 422 423
ЗНАКА ШАВОР 69-04.9С 033-21.1В НР 590="""

MURMANSK_CANCEL_PRIP = """\
ПРИП МУРМАНСК 88/26 КАРТА 15005
ОТМ 85/26
КОЛЬСКИЙ ЗАЛИВ
СНЕСЁН БУЙ
69-11.6С 033-34.9В="""

WEST_PRIP_FULL = """\
ПРИП ЗАПАД 42/26 КАРТА 11000 БАРЕНЦЕВО МОРЕ
ОТМ ЭТОТ НР 01 ИЮЛЬ 26=
ЗАТОНУВШЕЕ СУДНО ОБНАРУЖЕНО
70-10.0С 058-20.0В="""


# ── Tests: parse_prip_header ───────────────────────────────────────────


class TestParsePripHeader:
    """Unit tests for parse_prip_header()."""

    def test_murmansk_with_details(self) -> None:
        area, msg_id, year, maps, details = parse_prip_header(MURMANSK_HEADER_DETAILS)
        assert area.upper() == "МУРМАНСК"
        assert msg_id == "87"
        assert year == "26"
        assert "12000" in maps
        assert details is not None
        assert "МОТОВСКИЙ" in details.upper()

    def test_murmansk_without_details(self) -> None:
        area, msg_id, year, maps, details = parse_prip_header(
            MURMANSK_HEADER_NO_DETAILS
        )
        assert area.upper() == "МУРМАНСК"
        assert msg_id == "90"
        assert year == "26"
        assert "15005" in maps
        assert details is None

    def test_murmansk_multiple_charts(self) -> None:
        area, msg_id, year, maps, details = parse_prip_header(
            MURMANSK_HEADER_MULTI_CHARTS
        )
        assert area.upper() == "МУРМАНСК"
        assert msg_id == "290"
        assert year == "25"
        assert "15005" in maps
        assert "15006" in maps

    def test_arkhangelsk_short_form(self) -> None:
        """Regression: Arkhangelsk short headers previously
        returned all Nones."""
        area, msg_id, year, maps, details = parse_prip_header(ARKHANGELSK_HEADER_SHORT)
        assert area is not None, "Arkhangelsk short header must not return None"
        assert area.upper() == "АРХАНГЕЛЬСК"
        assert msg_id == "11"
        assert year == "26"
        assert "16007" in maps

    def test_arkhangelsk_no_space(self) -> None:
        """Regression: 'карта16022' with no space was
        not matched."""
        area, msg_id, year, maps, details = parse_prip_header(
            ARKHANGELSK_HEADER_NOSPACE
        )
        assert area is not None, "No-space chart headers must parse"
        assert area.upper() == "АРХАНГЕЛЬСК"
        assert msg_id == "6"
        assert year == "26"
        assert "16022" in maps

    def test_arkhangelsk_kniga(self) -> None:
        """Regression: книга keyword was not recognized."""
        area, msg_id, year, maps, details = parse_prip_header(ARKHANGELSK_HEADER_KNIGA)
        assert area is not None, "книга headers must parse"
        assert area.upper() == "АРХАНГЕЛЬСК"
        assert msg_id == "3"
        assert year == "26"
        assert "3030" in maps

    def test_arkhangelsk_kniga_uppercase(self) -> None:
        area, msg_id, year, maps, details = parse_prip_header(
            ARKHANGELSK_HEADER_KNIGA_UC
        )
        assert area is not None
        assert area.upper() == "АРХАНГЕЛЬСК"
        assert "3031" in maps

    def test_west_with_details(self) -> None:
        area, msg_id, year, maps, details = parse_prip_header(WEST_HEADER)
        assert area.upper() == "ЗАПАД"
        assert msg_id == "42"
        assert year == "26"
        assert "11000" in maps
        assert details is not None
        assert "БАРЕНЦЕВО" in details.upper()

    def test_invalid_header_returns_nones(self) -> None:
        result = parse_prip_header(INVALID_HEADER_NO_REGION)
        assert result == (None, None, None, None, None)

    def test_garbage_returns_nones(self) -> None:
        result = parse_prip_header(INVALID_HEADER_GARBAGE)
        assert result == (None, None, None, None, None)

    def test_case_insensitivity(self) -> None:
        """ПРИП regex must be case-insensitive for keywords."""
        header = "прип мурманск 1/26 карта 10000"
        area, msg_id, year, maps, _ = parse_prip_header(header)
        assert area is not None
        assert msg_id == "1"


# ── Tests: regression — no all-None returns for valid headers ──────────


# Every known valid header variant must produce non-None fields.
VALID_HEADERS = [
    MURMANSK_HEADER_DETAILS,
    MURMANSK_HEADER_NO_DETAILS,
    MURMANSK_HEADER_MULTI_CHARTS,
    ARKHANGELSK_HEADER_SHORT,
    ARKHANGELSK_HEADER_NOSPACE,
    ARKHANGELSK_HEADER_KNIGA,
    ARKHANGELSK_HEADER_KNIGA_UC,
    WEST_HEADER,
]


@pytest.mark.parametrize("header", VALID_HEADERS)
def test_valid_header_never_returns_all_nones(header: str) -> None:
    """Regression: valid PRIP headers must never return
    (None, None, None, None, None)."""
    result = parse_prip_header(header)
    assert result != (
        None,
        None,
        None,
        None,
        None,
    ), f"parse_prip_header returned all Nones for: {header!r}"


@pytest.mark.parametrize("header", VALID_HEADERS)
def test_valid_header_has_area_and_msg_id(header: str) -> None:
    area, msg_id, year, maps, _ = parse_prip_header(header)
    assert area is not None
    assert msg_id is not None
    assert year is not None


# ── Tests: prip_from_text factory ──────────────────────────────────────


class TestPripFromText:
    """Integration tests for NavwarnMessage.prip_from_text()."""

    def test_arkhangelsk_msg_id_format(self) -> None:
        """The constructed msg_id must include the region
        in English and numeric id/year."""
        lines = ARKHANGELSK_PRIP_FULL.strip().split("\n")
        header = lines[0]
        body = "\n".join(lines[1:])
        msg = NavwarnMessage.prip_from_text(header, body)
        assert msg.msg_id == "PRIP ARKHANGELSK 11/26"
        assert msg.year == 2026

    def test_arkhangelsk_coordinates_parsed(self) -> None:
        lines = ARKHANGELSK_PRIP_FULL.strip().split("\n")
        header = lines[0]
        body = "\n".join(lines[1:])
        msg = NavwarnMessage.prip_from_text(header, body)
        assert len(msg.coordinates) == 4
        # First coordinate: 64-37.0N 038-37.0E
        lat, lon = msg.coordinates[0]
        assert pytest.approx(lat, abs=0.01) == 64 + 37.0 / 60
        assert pytest.approx(lon, abs=0.01) == 38 + 37.0 / 60

    def test_arkhangelsk_geometry_polygon(self) -> None:
        """4 coordinates with РАЙОНЕ keyword -> polygon."""
        lines = ARKHANGELSK_PRIP_FULL.strip().split("\n")
        header = lines[0]
        body = "\n".join(lines[1:])
        msg = NavwarnMessage.prip_from_text(header, body)
        assert msg.geometry == "polygon"

    def test_murmansk_msg_id_format(self) -> None:
        lines = MURMANSK_PRIP_FULL.strip().split("\n")
        header = lines[0]
        body = "\n".join(lines[1:])
        msg = NavwarnMessage.prip_from_text(header, body)
        assert msg.msg_id == "PRIP MURMANSK 87/26"
        assert msg.year == 2026

    def test_west_msg_id_format(self) -> None:
        lines = WEST_PRIP_FULL.strip().split("\n")
        header = lines[0]
        body = "\n".join(lines[1:])
        msg = NavwarnMessage.prip_from_text(header, body)
        assert msg.msg_id == "PRIP WEST 42/26"
        assert msg.year == 2026

    def test_murmansk_cancellations(self) -> None:
        lines = MURMANSK_CANCEL_PRIP.strip().split("\n")
        header = lines[0]
        body = "\n".join(lines[1:])
        msg = NavwarnMessage.prip_from_text(header, body)
        assert "85/26" in msg.cancellations

    def test_west_self_cancel_date(self) -> None:
        """Self-cancellation with Russian date should be
        normalized."""
        lines = WEST_PRIP_FULL.strip().split("\n")
        header = lines[0]
        body = "\n".join(lines[1:])
        msg = NavwarnMessage.prip_from_text(header, body)
        assert any("THIS MSG" in c for c in msg.cancellations)


# ── Tests: parse_prips end-to-end ──────────────────────────────────────


class TestParsePrips:
    """Integration tests for parse_prips()."""

    def test_multiple_prips_unique_ids(self) -> None:
        """Each PRIP must produce a unique msg_id —
        regression for the bug where all Arkhangelsk PRIPs
        collapsed to PRIP_None_None_None_None."""
        raw_prips = [
            (
                "ПРИП АРХАНГЕЛЬСК 3/26 книга 3030",
                "ПРИП АРХАНГЕЛЬСК 3/26 книга 3030\nОГНИ",
            ),
            (
                "ПРИП АРХАНГЕЛЬСК 6/26 карта16022",
                "ПРИП АРХАНГЕЛЬСК 6/26 карта16022\nОГНИ",
            ),
            (
                "ПРИП АРХАНГЕЛЬСК 11/26 Карта 16007",
                "ПРИП АРХАНГЕЛЬСК 11/26 Карта 16007\nОГНИ",
            ),
        ]
        msgs = parse_prips(raw_prips)
        assert len(msgs) == 3
        ids = [m.msg_id for m in msgs]
        # All ids must be distinct
        assert len(set(ids)) == 3
        # No id should contain 'None'
        for mid in ids:
            assert "None" not in mid

    def test_mixed_regions(self) -> None:
        raw_prips = [
            (
                MURMANSK_PRIP_FULL.splitlines()[0],
                MURMANSK_PRIP_FULL,
            ),
            (
                ARKHANGELSK_PRIP_FULL.splitlines()[0],
                ARKHANGELSK_PRIP_FULL,
            ),
            (
                WEST_PRIP_FULL.splitlines()[0],
                WEST_PRIP_FULL,
            ),
        ]
        msgs = parse_prips(raw_prips)
        assert len(msgs) == 3
        regions = {m.msg_id.split()[1] for m in msgs}
        assert regions == {"MURMANSK", "ARKHANGELSK", "WEST"}

    def test_empty_input(self) -> None:
        assert parse_prips([]) == []


# ── Tests: prip_parse_cancellations ────────────────────────────────────


class TestPripParseCancellations:
    """Tests for cross-reference and self-cancellation parsing."""

    def test_cross_reference_cancel(self) -> None:
        body = "ОТМ 85/26\nКОЛЬСКИЙ ЗАЛИВ"
        cancels = prip_parse_cancellations(body, year="26")
        assert "85/26" in cancels

    def test_self_cancel_russian_date(self) -> None:
        body = "ОТМ ЭТОТ НР 01 ИЮЛЬ 26="
        cancels = prip_parse_cancellations(body, year="26")
        assert any("THIS MSG" in c for c in cancels)

    def test_no_cancellations(self) -> None:
        body = "ОГНИ НЕ ДЕЙСТВУЮТ\n69-09.8С 033-29.6В"
        cancels = prip_parse_cancellations(body, year="25")
        assert cancels == []
