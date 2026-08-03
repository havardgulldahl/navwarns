"""Tests for the Andøya OLX danger-zone scraper/parser."""

import gzip
from pathlib import Path

import pytest

from datetime import timezone

from scripts.scraper_andoya import (
    OlexRoute,
    feature_filename,
    olex_to_decimal_degrees,
    parse_active_period,
    parse_olx,
    route_to_geojson_feature,
)

TEST_DATA = Path(__file__).parent / "test_data"
OLX_GZ_PATH = TEST_DATA / "andoya_danger_zones.olx.gz"


# ---- Fixture: decompress and read the .olx.gz test file ----


@pytest.fixture(scope="module")
def olx_text() -> str:
    """Decompress the test .olx.gz fixture and return text."""
    raw = OLX_GZ_PATH.read_bytes()
    return gzip.decompress(raw).decode("latin-1")


@pytest.fixture(scope="module")
def routes(olx_text: str) -> list[OlexRoute]:
    return parse_olx(olx_text)


# ---- Decompression tests ----


class TestDecompression:
    def test_gz_fixture_exists(self):
        assert OLX_GZ_PATH.exists(), "test fixture .olx.gz missing"

    def test_decompress_produces_text(self, olx_text: str):
        assert len(olx_text) > 0

    def test_decompress_contains_olex_header(self, olx_text: str):
        assert "Ferdig forenklet" in olx_text

    def test_decompress_contains_route_marker(self, olx_text: str):
        assert "Rute ukjent" in olx_text

    def test_decompress_contains_coordinates(self, olx_text: str):
        assert "Brunsirkel" in olx_text

    def test_decompress_latin1_norwegian_chars(self, olx_text: str):
        assert "Fareområdet" in olx_text
        assert "Andøya" in olx_text


# ---- Coordinate conversion ----


class TestCoordinateConversion:
    def test_equator_greenwich(self):
        lat, lon = olex_to_decimal_degrees(0.0, 0.0)
        assert lat == 0.0
        assert lon == 0.0

    def test_sixty_minutes_is_one_degree(self):
        lat, lon = olex_to_decimal_degrees(60.0, 60.0)
        assert lat == pytest.approx(1.0)
        assert lon == pytest.approx(1.0)

    def test_negative_longitude(self):
        lat, lon = olex_to_decimal_degrees(4200.0, -600.0)
        assert lat == pytest.approx(70.0)
        assert lon == pytest.approx(-10.0)

    def test_sample_inner_first_vertex(self):
        # First coordinate from the inner area: 4139.332020 928.045020
        lat, lon = olex_to_decimal_degrees(4139.332020, 928.045020)
        assert lat == pytest.approx(68.9889, abs=0.001)
        assert lon == pytest.approx(15.4674, abs=0.001)


# ---- OLX parsing ----


class TestParseOlx:
    def test_two_unique_routes(self, routes: list[OlexRoute]):
        assert len(routes) == 2

    def test_inner_area_name(self, routes: list[OlexRoute]):
        names = {r.area_name for r in routes}
        assert "Danger Area Sea Inner" in names

    def test_outer_area_name(self, routes: list[OlexRoute]):
        names = {r.area_name for r in routes}
        assert "Danger Area Sea Outer" in names

    def test_inner_has_correct_vertex_count(self, routes: list[OlexRoute]):
        inner = next(r for r in routes if r.area_name == "Danger Area Sea Inner")
        # 4 unique vertices + closing vertex = 5
        assert len(inner.coordinates) == 5

    def test_outer_has_correct_vertex_count(self, routes: list[OlexRoute]):
        outer = next(r for r in routes if r.area_name == "Danger Area Sea Outer")
        # 10 unique vertices + closing vertex = 11
        assert len(outer.coordinates) == 11

    def test_all_routes_have_name(self, routes: list[OlexRoute]):
        for r in routes:
            assert r.name == "Andoya Danger Area"

    def test_inner_has_english_description(self, routes: list[OlexRoute]):
        inner = next(r for r in routes if r.area_name == "Danger Area Sea Inner")
        assert "danger area is active" in inner.description_en

    def test_inner_has_norwegian_description(self, routes: list[OlexRoute]):
        inner = next(r for r in routes if r.area_name == "Danger Area Sea Inner")
        assert "aktivt" in inner.description_no
        assert "Fareområdet" in inner.description_no

    def test_descriptions_are_not_duplicated(self, routes: list[OlexRoute]):
        for r in routes:
            # The english description should contain "danger area"
            # only a limited number of times (not doubled)
            count = r.description_en.lower().count("name: danger area")
            assert count == 1, (
                f"description_en appears duplicated: "
                f"'Name: Danger Area' found {count} times"
            )

    def test_inner_coordinates_in_norway(self, routes: list[OlexRoute]):
        inner = next(r for r in routes if r.area_name == "Danger Area Sea Inner")
        for lat, lon in inner.coordinates:
            assert 65.0 < lat < 75.0, f"lat {lat} out of range"
            assert 10.0 < lon < 20.0, f"lon {lon} out of range"

    def test_outer_extends_west_of_greenwich(self, routes: list[OlexRoute]):
        outer = next(r for r in routes if r.area_name == "Danger Area Sea Outer")
        lons = [lon for _, lon in outer.coordinates]
        assert min(lons) < 0, "outer area should extend west"

    def test_closed_polygon(self, routes: list[OlexRoute]):
        for r in routes:
            assert (
                r.coordinates[0] == r.coordinates[-1]
            ), f"polygon for {r.area_name} is not closed"


class TestParseOlxEdgeCases:
    def test_empty_input(self):
        assert parse_olx("") == []

    def test_no_routes(self):
        assert parse_olx("Ferdig forenklet\nsome text\n") == []

    def test_route_without_coordinates(self):
        text = "Rute ukjent\nFikspos\nNavn Test\n"
        assert parse_olx(text) == []

    def test_minimal_route(self):
        text = (
            "Rute ukjent\n"
            "100.0 200.0 1234567890 Brunsirkel\n"
            "300.0 400.0 1234567890 Brunsirkel\n"
            "500.0 600.0 1234567890 Brunsirkel\n"
            "100.0 200.0 1234567890 Brunsirkel\n"
            "Navn Testroute\n"
            "MTekst 0: Name: Test Zone\n"
        )
        routes = parse_olx(text)
        assert len(routes) == 1
        assert routes[0].area_name == "Test Zone"
        assert len(routes[0].coordinates) == 4

    def test_duplicate_routes_deduplicated(self):
        block = (
            "Rute ukjent\n"
            "100.0 200.0 1234567890 Brunsirkel\n"
            "300.0 400.0 1234567890 Brunsirkel\n"
            "500.0 600.0 1234567890 Brunsirkel\n"
            "Navn Same\n"
            "MTekst 0: Name: Same Zone\n"
        )
        text = block + "\n" + block
        routes = parse_olx(text)
        assert len(routes) == 1


# ---- GeoJSON Feature generation ----


class TestGeoJsonFeature:
    def test_feature_type(self, routes: list[OlexRoute]):
        feat = route_to_geojson_feature(routes[0])
        assert feat["type"] == "Feature"

    def test_feature_id_prefix(self, routes: list[OlexRoute]):
        for r in routes:
            feat = route_to_geojson_feature(r)
            assert feat["id"].startswith("ANDOYA_")

    def test_inner_feature_id(self, routes: list[OlexRoute]):
        inner = next(r for r in routes if r.area_name == "Danger Area Sea Inner")
        feat = route_to_geojson_feature(inner)
        assert feat["id"] == "ANDOYA_Danger_Area_Sea_Inner"

    def test_geometry_is_polygon(self, routes: list[OlexRoute]):
        for r in routes:
            feat = route_to_geojson_feature(r)
            assert feat["geometry"]["type"] == "Polygon"

    def test_geometry_ring_is_closed(self, routes: list[OlexRoute]):
        for r in routes:
            feat = route_to_geojson_feature(r)
            ring = feat["geometry"]["coordinates"][0]
            assert ring[0] == ring[-1]

    def test_geometry_coordinates_lonlat_order(self, routes: list[OlexRoute]):
        inner = next(r for r in routes if r.area_name == "Danger Area Sea Inner")
        feat = route_to_geojson_feature(inner)
        ring = feat["geometry"]["coordinates"][0]
        # GeoJSON is [lon, lat]; inner area lon ~15, lat ~69
        for lon, lat in ring:
            assert 10.0 < lon < 20.0
            assert 65.0 < lat < 75.0

    def test_properties_hazard_type(self, routes: list[OlexRoute]):
        feat = route_to_geojson_feature(routes[0])
        assert feat["properties"]["hazard_type"] == "firing_exercises"

    def test_properties_source(self, routes: list[OlexRoute]):
        feat = route_to_geojson_feature(routes[0])
        assert feat["properties"]["source"] == "BarentsWatch/Andoya"

    def test_properties_geometry_kind(self, routes: list[OlexRoute]):
        feat = route_to_geojson_feature(routes[0])
        assert feat["properties"]["geometry_kind"] == "polygon"

    def test_properties_body_not_empty(self, routes: list[OlexRoute]):
        for r in routes:
            feat = route_to_geojson_feature(r)
            assert len(feat["properties"]["body"]) > 0

    def test_properties_has_year(self, routes: list[OlexRoute]):
        feat = route_to_geojson_feature(routes[0])
        assert feat["properties"]["year"] >= 2025

    def test_valid_from_matches_active_period_start(self, routes: list[OlexRoute]):
        outer = next(r for r in routes if r.area_name == "Danger Area Sea Outer")
        feat = route_to_geojson_feature(outer)
        assert feat["properties"]["valid_from"].startswith("2026-03-19")

    def test_valid_until_matches_active_period_end(self, routes: list[OlexRoute]):
        outer = next(r for r in routes if r.area_name == "Danger Area Sea Outer")
        feat = route_to_geojson_feature(outer)
        assert feat["properties"]["valid_until"].startswith("2026-03-29")

    def test_valid_from_is_utc_iso(self, routes: list[OlexRoute]):
        feat = route_to_geojson_feature(routes[0])
        assert "+00:00" in feat["properties"]["valid_from"]

    def test_valid_until_is_utc_iso(self, routes: list[OlexRoute]):
        feat = route_to_geojson_feature(routes[0])
        assert "+00:00" in feat["properties"]["valid_until"]

    def test_year_reflects_active_period(self, routes: list[OlexRoute]):
        feat = route_to_geojson_feature(routes[0])
        assert feat["properties"]["year"] == 2026


# ---- Active period parsing ----


class TestParseActivePeriod:
    def test_standard_english_pattern(self):
        text = (
            "Description: The danger area is active 19.03-29.03.2026 with launch window"
        )
        start, end = parse_active_period(text)
        assert start is not None
        assert start.year == 2026 and start.month == 3 and start.day == 19

    def test_end_date_day_month_year(self):
        text = "The danger area is active 19.03-29.03.2026 with launch window"
        _, end = parse_active_period(text)
        assert end is not None
        assert end.year == 2026 and end.month == 3 and end.day == 29

    def test_valid_until_end_of_day(self):
        text = "The danger area is active 01.06-15.06.2025"
        _, end = parse_active_period(text)
        assert end.hour == 23 and end.minute == 59 and end.second == 59

    def test_valid_from_start_of_day(self):
        text = "The danger area is active 01.06-15.06.2025"
        start, _ = parse_active_period(text)
        assert start.hour == 0 and start.minute == 0 and start.second == 0

    def test_timestamps_are_utc(self):
        text = "The danger area is active 01.06-15.06.2025"
        start, end = parse_active_period(text)
        assert start.tzinfo == timezone.utc
        assert end.tzinfo == timezone.utc

    def test_norwegian_pattern(self):
        text = "Fareområdet er aktivt 19.03-29.03.2026 med skytevindu"
        start, end = parse_active_period(text)
        assert start is not None and end is not None
        assert start.month == 3 and end.month == 3

    def test_year_boundary_span(self):
        text = "The danger area is active 28.12-04.01.2027"
        start, end = parse_active_period(text)
        assert start.year == 2026 and start.month == 12 and start.day == 28
        assert end.year == 2027 and end.month == 1 and end.day == 4

    def test_full_dates_both_sides(self):
        text = "The danger area is active 15.06.2026-21.06.2026 with launch window"
        start, end = parse_active_period(text)
        assert start is not None and end is not None
        assert start.year == 2026 and start.month == 6 and start.day == 15
        assert end.year == 2026 and end.month == 6 and end.day == 21

    def test_full_start_date_year_used_directly(self):
        text = "The danger area is active 08.04.2026-19.04.2026"
        start, end = parse_active_period(text)
        assert start.year == 2026 and start.month == 4 and start.day == 8

    def test_no_match_returns_none_pair(self):
        assert parse_active_period("No active period here") == (None, None)

    def test_empty_string_returns_none_pair(self):
        assert parse_active_period("") == (None, None)

    # Pattern 2: "active on Month Dth"
    def test_active_on_single_day_with_backup(self):
        text = (
            "Description: The danger area is active on July 30th with launch window "
            "1245-1600 local time. Backup day is July 31st with launch window 0845-1600."
        )
        start, end = parse_active_period(text)
        assert start is not None and end is not None
        assert start.month == 7 and start.day == 30
        assert end.month == 7 and end.day == 31

    def test_active_on_single_day_no_backup(self):
        text = "The danger area is active on June 4th with launch window 1245-1600."
        start, end = parse_active_period(text)
        assert start is not None
        assert start.month == 6 and start.day == 4
        assert end.month == 6 and end.day == 4

    def test_active_on_uses_year_from_text_when_available(self):
        text = "June 2026. The danger area is active on June 4th with launch window."
        start, _ = parse_active_period(text)
        assert start.year == 2026

    # Pattern 3: "During the period D-D Month YYYY"
    def test_during_period_compressed_range(self):
        text = "During the period 10-14 May 2026, activities will take place off the coast."
        start, end = parse_active_period(text)
        assert start is not None and end is not None
        assert start.year == 2026 and start.month == 5 and start.day == 10
        assert end.month == 5 and end.day == 14

    # Pattern 4: "In the period D Month - D Month YYYY"
    def test_in_period_d_month_range(self):
        text = "In the period 4 May - 8 May 2026 there will be activity off the coast."
        start, end = parse_active_period(text)
        assert start is not None and end is not None
        assert start.year == 2026 and start.month == 5 and start.day == 4
        assert end.month == 5 and end.day == 8

    def test_in_period_d_month_range_different_months(self):
        text = "In the period 18 May - 2 June 2026 there will be activity."
        start, end = parse_active_period(text)
        assert start.month == 5 and start.day == 18
        assert end.month == 6 and end.day == 2

    # Pattern 5: "In the period Month Dth - Month Dth, YYYY"
    def test_in_period_month_ordinal_range(self):
        text = "In the period May 10th - May14th, 2026 there will be activity."
        start, end = parse_active_period(text)
        assert start is not None and end is not None
        assert start.year == 2026 and start.month == 5 and start.day == 10
        assert end.month == 5 and end.day == 14


# ---- Filename generation ----


class TestFeatureFilename:
    def test_safe_filename(self):
        assert feature_filename("ANDOYA_Test") == "ANDOYA_Test.json"

    def test_special_chars_replaced(self):
        name = feature_filename("ANDOYA Danger/Area")
        assert "/" not in name
        assert " " not in name
        assert name.endswith(".json")
