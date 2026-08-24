"""Tests for the Kystverket JSON API scraper (NAVAREA XIX + NAVCO coastal)."""

import json
from pathlib import Path

import pytest

from scripts import scraper_navareaxix_api as api
from scripts.parser import NavwarnMessage, classify_category

DATA_DIR = Path(__file__).parent / "data"


def _load(name):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def xix_records():
    return _load("api_kystverket_no_navarea_xix.json")


@pytest.fixture
def navco_records():
    return _load("api_kystverket_no_coastal.json")


def _by_number(records, number):
    return next(r for r in records if r["warningnumber"] == number)


class TestTimestamps:
    def test_dtg_from_timestamp(self):
        assert api.dtg_from_timestamp("2026-08-21T18:30:00") == "211830Z AUG 26"

    def test_dtg_from_timestamp_with_zulu_suffix(self):
        assert api.dtg_from_timestamp("2026-03-29T18:30:00Z") == "291830Z MAR 26"

    def test_dtg_from_timestamp_empty(self):
        assert api.dtg_from_timestamp(None) == ""
        assert api.dtg_from_timestamp("not a date") == ""

    def test_iso_utc_assumes_utc(self):
        assert api.iso_utc("2026-08-21T11:15:13.499692") == "2026-08-21T11:15:13.499692+00:00"


class TestWktGeometry:
    def test_polygon_ring_is_lon_lat_and_closed(self, xix_records):
        geom = api.wkt_to_geojson(_by_number(xix_records, "102/26")["position"])
        assert geom["type"] == "Polygon"
        ring = geom["coordinates"][0]
        assert ring[0] == [-5.0, 65.0]
        assert ring[0] == ring[-1]
        assert len(ring) == 11

    def test_multipoint_rig_positions(self, xix_records):
        geom = api.wkt_to_geojson(_by_number(xix_records, "103/26")["position"])
        assert geom["type"] == "MultiPoint"
        assert geom["coordinates"] == [
            [5.303333, 67.026667],
            [6.705, 65.133333],
            [7.301667, 65.286667],
            [20.358333, 72.545],
        ]

    def test_point(self, navco_records):
        geom = api.wkt_to_geojson(_by_number(navco_records, "287/2026")["position"])
        assert geom == {"type": "Point", "coordinates": [6.261233, 58.297399]}

    @pytest.mark.parametrize("bad", [None, "", "   ", "POLYGON((1 2", "NOTWKT(1 2)"])
    def test_unparseable_returns_none(self, bad):
        assert api.wkt_to_geojson(bad) is None

    def test_empty_geometry_returns_none(self):
        assert api.wkt_to_geojson("POLYGON EMPTY") is None

    def test_latlon_coords_are_flipped(self, xix_records):
        coords = api.wkt_to_latlon_coords(_by_number(xix_records, "103/26")["position"])
        assert coords[0] == (67.026667, 5.303333)
        assert len(coords) == 4

    def test_geojson_uses_lists_not_tuples(self, navco_records):
        geom = api.wkt_to_geojson(_by_number(navco_records, "287/2026")["position"])
        assert isinstance(geom["coordinates"], list)


class TestNavareaXixFeatures:
    def test_identity_and_year(self, xix_records):
        feat = api.record_to_feature(_by_number(xix_records, "102/26"), api.NAVAREAXIX)
        assert feat["id"] == "NAVAREA XIX 102/26"
        assert feat["properties"]["msg_id"] == "NAVAREA XIX 102/26"
        assert feat["properties"]["year"] == 2026

    def test_dtg_from_api_timestamp(self, xix_records):
        props = api.record_to_feature(
            _by_number(xix_records, "102/26"), api.NAVAREAXIX
        )["properties"]
        assert props["raw_dtg"] == "211830Z AUG 26"
        assert props["dtg"].startswith("2026-08-21T18:30:00")
        assert props["valid_from"].startswith("2026-08-21T18:30:00")

    def test_api_metadata_present(self, xix_records):
        props = api.record_to_feature(
            _by_number(xix_records, "102/26"), api.NAVAREAXIX
        )["properties"]
        assert props["eventid"] == 858436
        assert props["status"] == "active"
        assert props["eventclass"] == "NA19"
        assert props["updated_at"] == "2026-08-21T11:15:13.499692+00:00"
        assert props["source"] == "kystverket-api"
        assert props["summary"] is None

    def test_geometry_taken_from_api(self, xix_records):
        feat = api.record_to_feature(_by_number(xix_records, "102/26"), api.NAVAREAXIX)
        assert feat["properties"]["geometry_source"] == "api-wkt"
        assert feat["properties"]["geometry_kind"] == "polygon"
        assert feat["properties"]["radius_nm"] is None
        assert feat["geometry"] == api.wkt_to_geojson(
            _by_number(xix_records, "102/26")["position"]
        )

    def test_riglist_is_multipoint(self, xix_records):
        feat = api.record_to_feature(_by_number(xix_records, "103/26"), api.NAVAREAXIX)
        assert feat["geometry"]["type"] == "MultiPoint"
        assert feat["properties"]["geometry_kind"] == "multipoint"
        assert len(feat["geometry"]["coordinates"]) == 4

    def test_riglist_cancellations(self, xix_records):
        props = api.record_to_feature(
            _by_number(xix_records, "103/26"), api.NAVAREAXIX
        )["properties"]
        assert "NAVAREA XIX 100/26" in props["cancellations"]

    def test_body_keeps_original_api_casing(self, xix_records):
        record = _by_number(xix_records, "103/26")
        props = api.record_to_feature(record, api.NAVAREAXIX)["properties"]
        assert props["body"] == record["message_en"]

    def test_port_restriction_keeps_boundary_polygon(self, xix_records):
        feat = api.record_to_feature(_by_number(xix_records, "45/26"), api.NAVAREAXIX)
        assert feat["geometry"]["type"] == "Polygon"
        assert feat["properties"]["body"].startswith("NAVAREA XIX 45/26")

    def test_fallback_to_parsed_text_when_position_missing(self, xix_records):
        record = dict(_by_number(xix_records, "103/26"))
        record["position"] = None
        feat = api.record_to_feature(record, api.NAVAREAXIX)
        assert feat["properties"]["geometry_source"] == "parsed-text"
        assert feat["geometry"] is not None
        assert feat["geometry"]["type"] == "MultiPoint"

    def test_non_active_status_sets_valid_until(self, xix_records):
        record = dict(_by_number(xix_records, "102/26"))
        record["status"] = "cancelled"
        props = api.record_to_feature(record, api.NAVAREAXIX)["properties"]
        assert props["valid_until"] == props["updated_at"]

    def test_active_status_leaves_valid_until_to_parser(self, xix_records):
        record = _by_number(xix_records, "99/26")
        props = api.record_to_feature(record, api.NAVAREAXIX)["properties"]
        # "Cancel this msg 271900utc aug 26" is a parser-derived self-cancellation.
        assert props["valid_until"] is not None
        assert props["valid_until"] != props["updated_at"]

    def test_all_records_produce_valid_features(self, xix_records):
        for record in xix_records:
            feat = api.record_to_feature(record, api.NAVAREAXIX)
            assert feat["type"] == "Feature"
            assert feat["id"].startswith("NAVAREA XIX ")
            json.dumps(feat)  # must be JSON-serialisable


class TestParserParity:
    """The new API path must classify text exactly like the old text-only route."""

    @pytest.mark.parametrize("number", ["102/26", "103/26", "45/26", "99/26"])
    def test_matches_from_text_classification(self, xix_records, number):
        record = _by_number(xix_records, number)
        body = record["message_en"].upper()
        legacy = NavwarnMessage.from_text(api.dtg_from_timestamp(record["dtg_timestamp"]), body)
        legacy_props = legacy.to_geojson_feature()["properties"]
        new_props = api.record_to_feature(record, api.NAVAREAXIX)["properties"]

        for key in (
            "hazard_type",
            "category",
            "title",
            "cancellations",
            "inforce_warnings",
            "valid_from",
            "valid_until",
        ):
            assert new_props[key] == legacy_props[key], key

    @pytest.mark.parametrize("number", ["102/26", "103/26", "45/26", "99/26"])
    def test_text_geometry_matches_legacy_when_no_wkt(self, xix_records, number):
        """Without the API position the new path must reproduce the legacy geometry."""
        record = dict(_by_number(xix_records, number))
        record["position"] = None
        legacy = NavwarnMessage.from_text(
            api.dtg_from_timestamp(record["dtg_timestamp"]), record["message_en"].upper()
        )
        new_feat = api.record_to_feature(record, api.NAVAREAXIX)
        assert new_feat["geometry"] == legacy.to_geojson_feature()["geometry"]
        assert new_feat["properties"]["geometry_kind"] == legacy.geometry

    def test_msg_id_is_authoritative_not_regex(self, xix_records):
        record = dict(_by_number(xix_records, "45/26"))
        record["warningnumber"] = "45/26"
        # Body mentions "Navarea XIX 1/25"; the API number must still win.
        assert (
            api.record_to_feature(record, api.NAVAREAXIX)["id"] == "NAVAREA XIX 45/26"
        )

    def test_category_matches_hazard_classification(self, xix_records):
        props = api.record_to_feature(
            _by_number(xix_records, "99/26"), api.NAVAREAXIX
        )["properties"]
        category, title = classify_category(props["hazard_type"])
        assert (props["category"], props["title"]) == (category, title)


class TestNavcoFeatures:
    def test_identity(self, navco_records):
        feat = api.record_to_feature(_by_number(navco_records, "287/2026"), api.NAVCO)
        assert feat["id"] == "NAVCO 287/2026"
        assert feat["properties"]["year"] == 2026

    def test_four_digit_and_two_digit_years(self):
        assert api._year_from_warningnumber("287/2026") == 2026
        assert api._year_from_warningnumber("102/26") == 2026
        assert api._year_from_warningnumber("garbage") is None

    def test_coastal_metadata(self, navco_records):
        props = api.record_to_feature(
            _by_number(navco_records, "287/2026"), api.NAVCO
        )["properties"]
        assert props["location"] == "Rekefjordboen"
        assert props["norwegianchart"] == "12"
        assert props["fdv_objectid"] == 50761.0
        assert props["message_no"].startswith("REKEFJORDBOEN LYSBOEYE")
        assert props["eventclass"] == "NAVCO"
        assert props["source"] == "kystverket-api"

    def test_point_geometry_from_api(self, navco_records):
        feat = api.record_to_feature(_by_number(navco_records, "287/2026"), api.NAVCO)
        assert feat["geometry"] == {"type": "Point", "coordinates": [6.261233, 58.297399]}
        assert feat["properties"]["geometry_kind"] == "point"
        assert feat["properties"]["geometry_source"] == "api-wkt"

    def test_hazard_classified_from_text(self, navco_records):
        props = api.record_to_feature(
            _by_number(navco_records, "287/2026"), api.NAVCO
        )["properties"]
        assert props["hazard_type"] == "aid to navigation outage"

    def test_norwegian_fallback_when_english_missing(self, navco_records):
        feat = api.record_to_feature(_by_number(navco_records, "311/2026"), api.NAVCO)
        assert feat["properties"]["body"].startswith("BODOE HAVN")
        assert feat["properties"]["message_no"].startswith("BODOE HAVN")

    def test_null_position_yields_no_geometry(self, navco_records):
        feat = api.record_to_feature(_by_number(navco_records, "311/2026"), api.NAVCO)
        assert feat["properties"]["geometry_source"] == "parsed-text"
        assert feat["geometry"] is None

    def test_cancelled_record_gets_valid_until(self, navco_records):
        props = api.record_to_feature(
            _by_number(navco_records, "312/2026"), api.NAVCO
        )["properties"]
        assert props["status"] == "cancelled"
        assert props["valid_until"] == "2026-08-23T14:20:00+00:00"


class TestFilenames:
    def test_navarea_xix_filename_matches_cleanup_glob(self, xix_records):
        feat = api.record_to_feature(_by_number(xix_records, "102/26"), api.NAVAREAXIX)
        name = f"{api.safe_filename(feat['id'])}.json"
        assert name == "NAVAREA_XIX_102_26.json"
        assert Path(name).match(api.NAVAREAXIX.cleanup_glob)
        assert not Path(name).match(api.NAVCO.cleanup_glob)

    def test_navco_filename_matches_cleanup_glob(self, navco_records):
        feat = api.record_to_feature(_by_number(navco_records, "287/2026"), api.NAVCO)
        name = f"{api.safe_filename(feat['id'])}.json"
        assert name == "NAVCO_287_2026.json"
        assert Path(name).match(api.NAVCO.cleanup_glob)
        assert not Path(name).match(api.NAVAREAXIX.cleanup_glob)


class TestRunSource:
    def test_writes_features_raw_snapshot_and_timestamp(self, tmp_path, xix_records):
        current = tmp_path / "current"
        history = tmp_path / "history"
        api.run_source(api.NAVAREAXIX, records=xix_records, current_dir=current, history_dir=history)

        written = sorted(p.name for p in (current / "navwarns").glob("*.json"))
        assert written == [
            "NAVAREA_XIX_102_26.json",
            "NAVAREA_XIX_103_26.json",
            "NAVAREA_XIX_45_26.json",
            "NAVAREA_XIX_99_26.json",
        ]
        for path in (current / "navwarns").glob("*.json"):
            feat = json.loads(path.read_text(encoding="utf-8"))
            assert feat["type"] == "Feature"

        assert (current / ".scrape_timestamp_NAVAREAXIX").read_text().strip()
        assert list(history.glob("*/NAVAREAXIX/NAVAREAXIX_*.json"))

    def test_rerun_is_idempotent(self, tmp_path, xix_records):
        current = tmp_path / "current"
        history = tmp_path / "history"
        api.run_source(api.NAVAREAXIX, records=xix_records, current_dir=current, history_dir=history)
        target = current / "navwarns" / "NAVAREA_XIX_102_26.json"
        before = target.stat().st_mtime_ns
        api.run_source(api.NAVAREAXIX, records=xix_records, current_dir=current, history_dir=history)
        assert target.stat().st_mtime_ns == before

    def test_sources_do_not_delete_each_other(self, tmp_path, xix_records, navco_records):
        current = tmp_path / "current"
        history = tmp_path / "history"
        api.run_source(api.NAVAREAXIX, records=xix_records, current_dir=current, history_dir=history)
        api.run_source(api.NAVCO, records=navco_records, current_dir=current, history_dir=history)

        names = {p.name for p in (current / "navwarns").glob("*.json")}
        assert "NAVAREA_XIX_102_26.json" in names
        assert "NAVCO_287_2026.json" in names
        assert len([n for n in names if n.startswith("NAVCO_")]) == len(navco_records)

    def test_disappeared_warning_moved_to_history(self, tmp_path, xix_records):
        current = tmp_path / "current"
        history = tmp_path / "history"
        api.run_source(api.NAVAREAXIX, records=xix_records, current_dir=current, history_dir=history)
        remaining = [r for r in xix_records if r["warningnumber"] != "45/26"]
        api.run_source(api.NAVAREAXIX, records=remaining, current_dir=current, history_dir=history)

        assert not (current / "navwarns" / "NAVAREA_XIX_45_26.json").exists()
        assert (history / "2026" / "navwarns" / "NAVAREA_XIX_45_26.json").exists()


class TestMain:
    def test_all_sources_fetched(self, monkeypatch, tmp_path, xix_records, navco_records):
        payloads = {
            api.NAVAREAXIX_URL: xix_records,
            api.COASTAL_URL: navco_records,
        }
        monkeypatch.setattr(api, "CURRENT_DIR", tmp_path / "current")
        monkeypatch.setattr(api, "HISTORY_DIR", tmp_path / "history")
        monkeypatch.setattr(api, "fetch_records", lambda source: payloads[source.url])

        api.main(["--source", "all"])

        assert (tmp_path / "current" / ".scrape_timestamp_NAVAREAXIX").exists()
        assert (tmp_path / "current" / ".scrape_timestamp_NAVCO").exists()

    def test_single_source(self, monkeypatch, tmp_path, xix_records):
        monkeypatch.setattr(api, "CURRENT_DIR", tmp_path / "current")
        monkeypatch.setattr(api, "HISTORY_DIR", tmp_path / "history")
        monkeypatch.setattr(api, "fetch_records", lambda source: xix_records)

        api.main(["--source", "navareaxix"])

        assert (tmp_path / "current" / ".scrape_timestamp_NAVAREAXIX").exists()
        assert not (tmp_path / "current" / ".scrape_timestamp_NAVCO").exists()

    def test_fetch_records_rejects_non_array(self, monkeypatch):
        class Resp:
            def json(self):
                return {"detail": "boom"}

        monkeypatch.setattr(api, "fetch", lambda url: Resp())
        with pytest.raises(ValueError):
            api.fetch_records(api.NAVAREAXIX)
