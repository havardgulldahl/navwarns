"""Cancellation logic tests: cross-cancellation vs self-cancellation.

Primary target: HYDROARC 61/26 which carries both a cross-cancel reference
(item 2: CANCEL HYDROARC 60/26) and a self-cancel DTG (item 3: CANCEL THIS
MSG 300059Z MAY 26).  The critical distinction under test is that the
cross-cancel ref ends up in `cancellations[]` but must NOT influence
`valid_until`, which is driven solely by the self-cancel DTG.

Corpus extensions cover:
  - English pure cross-cancel body (HYDROARC 333/23 pattern)
  - English comma-separated multiple cross-cancels (HYDROARC 325/23 pattern)
  - Russian PRIP pure cross-cancel (ОТМ NNN/YY, PRIP 213/26 pattern)
  - Russian PRIP operational + cross-cancel with normalised self-cancel (PRIP 385/25 pattern)
"""

from datetime import datetime, timezone

import pytest

from scripts.build_archives import _compute_valid_until as ba_compute_valid_until
from scripts.parser import (
    NavwarnMessage,
    parse_cancellations,
    parse_navwarns,
    prip_parse_cancellations,
)

# ---------------------------------------------------------------------------
# Sample messages
# ---------------------------------------------------------------------------

SAMPLE_HYDROARC_61_26 = """\
HYDROARC 61/26.
NORWEGIAN SEA.
NORWAY.
1. HAZARDOUS OPERATIONS 180001Z TO 292359Z MAY
   IN AREA BOUND BY
   65-30.00N 008-00.00W, 65-30.00N 012-00.00E,
   57-30.00N 012-00.00E, 57-30.00N 008-00.00W.
2. CANCEL HYDROARC 60/26.
3. CANCEL THIS MSG 300059Z MAY 26."""

# Pure-cancel body: no operational content, no self-cancel DTG.
# Source: HYDROARC 333/23 pattern from navArea=C corpus.
SAMPLE_PURE_CANCEL_EN = """\
HYDROARC 333/23(43).
BARENTS SEA.
RUSSIA.
DNC 22.
CANCEL HYDROARC 329/23 AND THIS MSG."""

# Multiple cross-cancels on a single line.
# Source: HYDROARC 325/23 pattern — comma-separated list then "AND THIS MSG".
SAMPLE_MULTI_CANCEL_EN = """\
HYDROARC 325/23(43).
BARENTS SEA.
RUSSIA.
DNC 22.
CANCEL HYDROARC 316/23, 317/23, AND THIS MSG."""

# Russian PRIP: pure cross-cancel, no operational area.
# Source: PRIP MURMANSK 213/26 pattern.
SAMPLE_PRIP_PURE_CANCEL_RU = """\
1. ОТМ 209/26
2. ОТМ ЭТОТ НР=
221000 МСК  ГС-"""

# Russian PRIP: has coordinates + cross-cancel + normalised self-cancel.
# Source: PRIP MURMANSK 385/25 pattern.
SAMPLE_PRIP_OPERATIONAL_CANCEL_RU = """\
БАРЕНЦЕВО МОРЕ
1. СТРЕЛЬБЫ РАКЕТНЫЕ 03 ПО 06 ДЕК 0300 ДО 1700
   ПЛАВАНИЕ ЗАПРЕЩЕНО ТЕРВОДАХ
   68-42.0С 048-40.0В
   68-28.0С 048-38.0В
   68-08.0С 050-08.0В
2. ОТМ ЭТОТ НР 061800 ДЕК
3. ОТМ 379/25 И ЭТОТ ПУНКТ=
011000 МСК ГС-"""

# Polish NAV WARN: yearless cancel with space before UTC (no Z, no year).
# Source: POLISH NAV WARN 049/26 — "CANCEL THIS MSG 212259 UTC MAR"
SAMPLE_POLISH_049_26 = """\
[Southern Baltic] POLISH NAV WARN 049/26
SOUTHERN BALTIC. POLISH COAST
DUE TO MILITARY EXERCISES ZONE IS CLOSED FOR SHIPPING AND FISHERY:
S-6 CENTERED 54-39.27N 016-36.59E
FROM 160400 MAR UNTIL 210059 MAR
AND 210400 MAR TO 211300 MAR
AND 211800 MAR TO 212259 MAR
TIME IN UTC
ZONE IS CLOSED
CANCEL THIS MSG 212259 UTC MAR"""

_VALID_UNTIL_61_26 = "2026-05-30T00:59:00+00:00"


# ---------------------------------------------------------------------------
# HYDROARC 61/26 — parser level
# ---------------------------------------------------------------------------


class TestHydroarc6126Parsing:
    """Parser-level assertions for HYDROARC 61/26."""

    @pytest.fixture(scope="class")
    def msg(self) -> NavwarnMessage:
        msgs = parse_navwarns(SAMPLE_HYDROARC_61_26)
        assert len(msgs) == 1
        return msgs[0]

    def test_msg_id(self, msg: NavwarnMessage) -> None:
        assert msg.msg_id == "HYDROARC 61/26"

    def test_year(self, msg: NavwarnMessage) -> None:
        assert msg.year == 2026

    def test_hazard_type(self, msg: NavwarnMessage) -> None:
        assert msg.hazard_type == "hazardous operations"

    def test_geometry_is_polygon(self, msg: NavwarnMessage) -> None:
        assert msg.geometry == "polygon"

    def test_coordinate_count(self, msg: NavwarnMessage) -> None:
        assert len(msg.coordinates) == 4

    def test_first_coordinate(self, msg: NavwarnMessage) -> None:
        # 65-30.00N 008-00.00W → lat 65.5, lon -8.0
        lat, lon = msg.coordinates[0]
        assert lat == pytest.approx(65.5, abs=0.001)
        assert lon == pytest.approx(-8.0, abs=0.001)

    def test_cross_cancel_ref_present(self, msg: NavwarnMessage) -> None:
        # Item 2: CANCEL HYDROARC 60/26 must appear as a cross-reference.
        assert "HYDROARC 60/26" in msg.cancellations

    def test_self_cancel_present(self, msg: NavwarnMessage) -> None:
        # Item 3: THIS MSG 300059Z MAY 26 must be captured.
        assert any("THIS MSG" in c for c in msg.cancellations)

    def test_valid_until_is_self_cancel_dtg(self, msg: NavwarnMessage) -> None:
        # Cross-cancel ref must not contaminate valid_until; self-cancel DTG must.
        result = msg._compute_valid_until()
        assert result is not None
        dt = datetime.fromisoformat(result)
        assert dt == datetime(2026, 5, 30, 0, 59, tzinfo=timezone.utc)

    def test_cross_cancel_alone_yields_no_valid_until(self) -> None:
        # A message whose only cancellations entry is a cross-ref must not produce a valid_until.
        msg = NavwarnMessage(
            dtg=None,
            raw_dtg="",
            msg_id="HYDROARC 60/26",
            cancellations=["HYDROARC 59/26"],
            year=2026,
        )
        assert msg._compute_valid_until() is None


# ---------------------------------------------------------------------------
# HYDROARC 61/26 — GeoJSON output
# ---------------------------------------------------------------------------


class TestHydroarc6126GeoJSON:
    """GeoJSON feature output for HYDROARC 61/26."""

    @pytest.fixture(scope="class")
    def features(self) -> list:
        msgs = parse_navwarns(SAMPLE_HYDROARC_61_26)
        return msgs[0].to_geojson_features()

    def test_single_feature(self, features: list) -> None:
        assert len(features) == 1

    def test_geometry_type_polygon(self, features: list) -> None:
        assert features[0]["geometry"]["type"] == "Polygon"

    def test_polygon_ring_closes(self, features: list) -> None:
        # Outer ring: 4 unique vertices + closing point = 5 entries.
        ring = features[0]["geometry"]["coordinates"][0]
        assert len(ring) == 5

    def test_first_ring_vertex(self, features: list) -> None:
        # GeoJSON uses [lon, lat] order.
        lon, lat = features[0]["geometry"]["coordinates"][0][0]
        assert lon == pytest.approx(-8.0, abs=0.001)
        assert lat == pytest.approx(65.5, abs=0.001)

    def test_valid_until_in_properties(self, features: list) -> None:
        assert features[0]["properties"]["valid_until"] == _VALID_UNTIL_61_26

    def test_cancellations_in_properties(self, features: list) -> None:
        cancels = features[0]["properties"]["cancellations"]
        assert "HYDROARC 60/26" in cancels


# ---------------------------------------------------------------------------
# build_archives._compute_valid_until — props-dict level
# ---------------------------------------------------------------------------


class TestBuildArchivesValidUntil:
    """build_archives._compute_valid_until correctly separates cross-refs from self-cancels."""

    def test_self_cancel_extracted_from_mixed_list(self) -> None:
        props = {
            "cancellations": ["HYDROARC 60/26", "THIS MSG 300059Z MAY 26"],
            "year": 2026,
        }
        result = ba_compute_valid_until(props)
        assert result is not None
        dt = datetime.fromisoformat(result)
        assert dt == datetime(2026, 5, 30, 0, 59, tzinfo=timezone.utc)

    def test_cross_ref_alone_yields_no_valid_until(self) -> None:
        props = {"cancellations": ["HYDROARC 60/26"], "year": 2026}
        assert ba_compute_valid_until(props) is None

    def test_self_cancel_without_cross_ref(self) -> None:
        props = {"cancellations": ["THIS MSG 300059Z MAY 26"], "year": 2026}
        result = ba_compute_valid_until(props)
        assert result == _VALID_UNTIL_61_26


# ---------------------------------------------------------------------------
# English corpus: pure cross-cancel body (HYDROARC 333/23 pattern)
# ---------------------------------------------------------------------------


class TestEnglishPureCrossCancel:
    """HYDROARC 333/23 pattern: body contains only a cross-cancel + bare THIS MSG (no DTG)."""

    @pytest.fixture(scope="class")
    def msg(self) -> NavwarnMessage:
        msgs = parse_navwarns(SAMPLE_PURE_CANCEL_EN)
        assert len(msgs) == 1
        return msgs[0]

    def test_cross_ref_captured(self, msg: NavwarnMessage) -> None:
        assert "HYDROARC 329/23" in msg.cancellations

    def test_year(self, msg: NavwarnMessage) -> None:
        assert msg.year == 2023

    def test_no_coordinates(self, msg: NavwarnMessage) -> None:
        assert len(msg.coordinates) == 0

    def test_bare_this_msg_yields_no_valid_until(self, msg: NavwarnMessage) -> None:
        # "AND THIS MSG" without a DTG must not produce a valid_until.
        assert msg._compute_valid_until() is None

    def test_parse_cancellations_direct(self) -> None:
        cancels = parse_cancellations(SAMPLE_PURE_CANCEL_EN)
        assert "HYDROARC 329/23" in cancels
        # No self-cancel DTG → THIS MSG entry absent.
        assert not any("THIS MSG" in c for c in cancels)


# ---------------------------------------------------------------------------
# English corpus: comma-separated multiple cross-cancels (HYDROARC 325/23)
# ---------------------------------------------------------------------------


class TestEnglishMultipleCrossCancels:
    """HYDROARC 325/23 pattern: two prior messages cancelled on one line."""

    @pytest.fixture(scope="class")
    def cancels(self) -> list:
        msgs = parse_navwarns(SAMPLE_MULTI_CANCEL_EN)
        return msgs[0].cancellations

    def test_first_cross_ref_structured(self, cancels: list) -> None:
        # CANCEL_PATTERN captures the structured form for the first entry.
        assert "HYDROARC 316/23" in cancels

    def test_second_plain_ref_captured(self, cancels: list) -> None:
        # Heuristic extracts the plain NNN/YY token for the second entry.
        assert "317/23" in cancels

    def test_no_valid_until(self) -> None:
        msgs = parse_navwarns(SAMPLE_MULTI_CANCEL_EN)
        assert msgs[0]._compute_valid_until() is None


# ---------------------------------------------------------------------------
# Russian PRIP corpus: pure cross-cancel (PRIP 213/26 pattern)
# ---------------------------------------------------------------------------


class TestRussianPripPureCrossCancel:
    """PRIP 213/26 pattern: ОТМ NNN/YY with bare ОТМ ЭТОТ НР (no time)."""

    def test_cross_ref_captured(self) -> None:
        cancels = prip_parse_cancellations(SAMPLE_PRIP_PURE_CANCEL_RU)
        assert "209/26" in cancels

    def test_bare_otm_etot_nr_yields_no_self_cancel(self) -> None:
        # "ОТМ ЭТОТ НР=" without a time token cannot produce a self-cancel date.
        cancels = prip_parse_cancellations(SAMPLE_PRIP_PURE_CANCEL_RU)
        assert not any("THIS MSG" in c for c in cancels)


# ---------------------------------------------------------------------------
# Russian PRIP corpus: operational + cross-cancel (PRIP 385/25 pattern)
# ---------------------------------------------------------------------------


class TestRussianPripOperationalCrossCancel:
    """PRIP 385/25 pattern: active area + ОТМ NNN/YY + ОТМ ЭТОТ НР with DTG."""

    @pytest.fixture(scope="class")
    def cancels(self) -> list:
        return prip_parse_cancellations(SAMPLE_PRIP_OPERATIONAL_CANCEL_RU, year="25")

    def test_cross_ref_captured(self, cancels: list) -> None:
        assert "379/25" in cancels

    def test_self_cancel_normalised(self, cancels: list) -> None:
        # ОТМ ЭТОТ НР 061800 ДЕК → normalised to English THIS MSG form.
        assert any("THIS MSG" in c for c in cancels)

    def test_self_cancel_dtg_dec_25(self, cancels: list) -> None:
        # Verify the normalised self-cancel parses to 2025-12-06T18:00:00Z.
        self_cancel = next(c for c in cancels if "THIS MSG" in c)
        msg = NavwarnMessage(
            dtg=None,
            raw_dtg="",
            msg_id="PRIP TEST",
            cancellations=[self_cancel],
            year=2025,
        )
        result = msg._compute_valid_until()
        assert result is not None
        dt = datetime.fromisoformat(result)
        assert dt == datetime(2025, 12, 6, 18, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Polish corpus: yearless UTC cancel (POLISH NAV WARN 049/26 pattern)
# ---------------------------------------------------------------------------


class TestPolishYearlessUtcCancel:
    """POLISH NAV WARN 049/26: 'CANCEL THIS MSG 212259 UTC MAR' — yearless, space before UTC.

    Regression: CANCEL_PATTERN previously fell through to the bare 'THIS MSG'
    fallback (losing the date), and the yearless regex in _compute_valid_until
    only handled Z, not UTC.
    """

    def test_cancellation_captures_dtg(self) -> None:
        cancels = parse_cancellations(SAMPLE_POLISH_049_26)
        assert cancels == ["THIS MSG 212259 UTC MAR"]

    def test_valid_until_inferred_from_issue_year(self) -> None:
        msg = NavwarnMessage.from_text("121125Z MAR 26", SAMPLE_POLISH_049_26)
        result = msg._compute_valid_until()
        assert result is not None
        dt = datetime.fromisoformat(result)
        assert dt == datetime(2026, 3, 21, 22, 59, tzinfo=timezone.utc)
