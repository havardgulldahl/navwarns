"""Tests for scripts/build_archives.py.

Covers property enrichment, feature collection, archive
building, and manifest generation using temporary directories.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.build_archives import (
    _apply_last_seen,
    _compute_valid_from,
    _compute_valid_until,
    _enrich_properties,
    _resolve_cross_cancellations,
    _scan_daily_presence,
    collect_features,
    build_archive,
    build_manifest,
)

# ------------------------------------------------------------------
# _compute_valid_from
# ------------------------------------------------------------------


class TestComputeValidFrom:
    """Tests for _compute_valid_from."""

    def test_from_iso_dtg(self) -> None:
        props = {"dtg": "2025-09-19T23:59:00+00:00"}
        assert _compute_valid_from(props) == ("2025-09-19T23:59:00+00:00")

    def test_from_dtg_with_z_suffix(self) -> None:
        props = {"dtg": "2025-09-19T23:59:00Z"}
        result = _compute_valid_from(props)
        assert result is not None
        assert "2025-09-19" in result

    def test_from_naive_dtg(self) -> None:
        props = {"dtg": "2025-09-19T23:59:00"}
        result = _compute_valid_from(props)
        assert result is not None
        assert "2025-09-19" in result

    def test_falls_back_to_year(self) -> None:
        props = {"year": 2023}
        result = _compute_valid_from(props)
        assert result == "2023-01-01T00:00:00+00:00"

    def test_year_as_string(self) -> None:
        props = {"year": "2020"}
        result = _compute_valid_from(props)
        assert result == "2020-01-01T00:00:00+00:00"

    def test_no_dtg_no_year(self) -> None:
        assert _compute_valid_from({}) is None

    def test_invalid_dtg_string(self) -> None:
        props = {"dtg": "not-a-date"}
        # Falls through to year; no year → returns the
        # raw string as-is (isinstance check).
        result = _compute_valid_from(props)
        assert result == "not-a-date"

    def test_from_to_day_range_with_trailing_month_year(self) -> None:
        props = {
            "body": "ROCKET OPERATIONS FROM 15 TO 21 JUN 26.",
            "year": 2026,
        }
        result = _compute_valid_from(props)
        assert result == "2026-06-15T00:00:00+00:00"


# ------------------------------------------------------------------
# _compute_valid_until
# ------------------------------------------------------------------


class TestComputeValidUntil:
    """Tests for _compute_valid_until."""

    def test_full_dtg_z(self) -> None:
        props = {
            "cancellations": ["THIS MSG 171600Z SEP 25"],
        }
        result = _compute_valid_until(props)
        assert result is not None
        dt = datetime.fromisoformat(result)
        assert dt == datetime(
            2025,
            9,
            17,
            16,
            0,
            tzinfo=timezone.utc,
        )

    def test_full_dtg_utc(self) -> None:
        props = {
            "cancellations": [
                "THIS MSG 141500 UTC SEP 25",
            ],
        }
        result = _compute_valid_until(props)
        assert result is not None
        dt = datetime.fromisoformat(result)
        assert dt.day == 14 and dt.hour == 15

    def test_date_only(self) -> None:
        props = {
            "cancellations": ["THIS MSG 01 JAN 20"],
        }
        result = _compute_valid_until(props)
        assert result is not None
        dt = datetime.fromisoformat(result)
        assert dt == datetime(
            2020,
            1,
            1,
            tzinfo=timezone.utc,
        )

    def test_message_variant(self) -> None:
        props = {
            "cancellations": [
                "THIS MESSAGE 010900 UTC MAR 19",
            ],
        }
        result = _compute_valid_until(props)
        assert result is not None
        dt = datetime.fromisoformat(result)
        assert dt.month == 3 and dt.hour == 9

    def test_no_self_cancellation(self) -> None:
        props = {
            "cancellations": [
                "101/24",
                "HYDROARC 119/25",
            ],
        }
        assert _compute_valid_until(props) is None

    def test_empty_cancellations(self) -> None:
        assert _compute_valid_until({}) is None
        assert _compute_valid_until({"cancellations": []}) is None

    def test_skips_non_self_entries(self) -> None:
        """Ignores plain references and parses the self-cancel token."""
        props = {
            "cancellations": [
                "101/24",
                "THIS MSG 050000Z JUL 26",
            ],
        }
        result = _compute_valid_until(props)
        assert result is not None
        dt = datetime.fromisoformat(result)
        assert dt == datetime(
            2026,
            7,
            5,
            0,
            0,
            tzinfo=timezone.utc,
        )

    def test_bare_cancel_dtg_with_year(self) -> None:
        props = {
            "cancellations": ["212215Z APR 16"],
        }
        result = _compute_valid_until(props)
        assert result is not None
        dt = datetime.fromisoformat(result)
        assert dt == datetime(
            2016,
            4,
            21,
            22,
            15,
            tzinfo=timezone.utc,
        )

    def test_bare_cancel_dtg_without_year_uses_dtg_year(self) -> None:
        props = {
            "dtg": "2026-04-17T05:58:00+00:00",
            "cancellations": ["242100 UTC APR"],
            "year": 2026,
        }
        result = _compute_valid_until(props)
        assert result is not None
        dt = datetime.fromisoformat(result)
        assert dt == datetime(
            2026,
            4,
            24,
            21,
            0,
            tzinfo=timezone.utc,
        )

    def test_from_to_day_range_with_trailing_month_year(self) -> None:
        props = {
            "body": "OPERATIONS DAILY FROM 15 TO 21 JUN 26.",
            "year": 2026,
        }
        result = _compute_valid_until(props)
        assert result == "2026-06-21T00:00:00+00:00"

    def test_from_to_day_time_range_with_trailing_month_year(self) -> None:
        props = {
            "body": "AREA TEMPORARILY DANGEROUS FROM 14 2100 TO 18 2100 UTC JUL 26.",
            "year": 2026,
        }
        result = _compute_valid_until(props)
        assert result == "2026-07-18T21:00:00+00:00"

    def test_cancel_date_precedence_when_consistent(self) -> None:
        props = {
            "valid_from": "2012-03-01T00:00:00+00:00",
            "cancel_date": "2012-03-05T00:01:00+00:00",
            "cancellations": [],
            "body": "",
        }
        result = _compute_valid_until(props)
        assert result == "2012-03-05T00:01:00+00:00"

    def test_ignores_cancel_date_before_valid_from(self) -> None:
        props = {
            "valid_from": "2012-03-31T02:03:00+00:00",
            "dtg": "2012-03-31T02:03:00",
            "year": 2012,
            "cancel_date": "2012-03-05T00:01:00+00:00",
            "cancellations": [],
            "body": "3. CANCEL THIS MSG 05 APR.",
        }
        result = _compute_valid_until(props)
        assert result == "2012-04-05T00:00:00+00:00"

    def test_yearless_date_only_self_cancel_in_body(self) -> None:
        props = {
            "dtg": "2012-03-31T02:03:00",
            "year": 2012,
            "cancellations": [],
            "body": "3. CANCEL THIS MSG 05 APR.",
        }
        result = _compute_valid_until(props)
        assert result == "2012-04-05T00:00:00+00:00"

    def test_cancel_date_later_than_valid_until_is_ignored(self) -> None:
        props = {
            "valid_from": "2012-03-31T02:03:00+00:00",
            "valid_until": "2012-04-05T00:00:00+00:00",
            "dtg": "2012-03-31T02:03:00",
            "year": 2012,
            "cancel_date": "2012-04-06T00:00:00+00:00",
            "cancellations": [],
            "body": "3. CANCEL THIS MSG 05 APR.",
        }
        result = _compute_valid_until(props)
        assert result == "2012-04-05T00:00:00+00:00"

    def test_self_cancel_before_valid_from_treated_as_typo_plus_24h(self) -> None:
        props = {
            "valid_from": "2024-04-22T13:05:00+00:00",
            "dtg": "2024-04-22T13:05:00",
            "year": 2024,
            "cancellations": [],
            "body": "2. CANCEL THIS MSG 221259Z APR 24.",
            "corrections": [],
        }
        result = _compute_valid_until(props)
        assert result == "2024-04-23T13:05:00+00:00"
        assert any(
            c.get("code") == "self_cancel_before_valid_from"
            and c.get("field") == "cancel_date"
            for c in props["corrections"]
        )


# ------------------------------------------------------------------
# _enrich_properties
# ------------------------------------------------------------------


class TestEnrichProperties:
    """Tests for _enrich_properties."""

    def test_adds_missing_fields(self) -> None:
        props = {
            "dtg": "2024-06-01T00:00:00Z",
            "cancellations": ["THIS MSG 010000Z JUL 24"],
        }
        enriched = _enrich_properties(props)
        assert enriched["valid_from"] is not None
        assert enriched["valid_until"] is not None

    def test_preserves_existing_values(self) -> None:
        props = {
            "valid_from": "custom-from",
            "valid_until": "custom-until",
        }
        enriched = _enrich_properties(props)
        assert enriched["valid_from"] == "custom-from"
        assert enriched["valid_until"] == "custom-until"

    def test_overwrites_none_values(self) -> None:
        props = {
            "valid_from": None,
            "valid_until": None,
            "year": 2022,
        }
        enriched = _enrich_properties(props)
        assert enriched["valid_from"] is not None

    def test_andoya_active_on_and_backup_override_scrape_dtg(self) -> None:
        props = {
            "msg_id": "ANDOYA_Fareomr_de_Studentrakett_And_ya_Space",
            "dtg": "2026-08-03T12:34:00",
            "year": 2026,
            "body": (
                "Name: Danger Area ESC And\u00f8ya Space Description: "
                "The danger area is active on July 30th with launch window "
                "1245-1600 local time. Backup day is July 31st with launch window "
                "0845-1600 local time."
            ),
            "valid_from": "2026-08-03T12:34:00+00:00",
            "valid_until": None,
        }
        enriched = _enrich_properties(props)
        assert enriched["valid_from"] == "2026-07-30T00:00:00+00:00"
        assert enriched["valid_until"] == "2026-07-31T23:59:59+00:00"

    def test_enrich_clears_cancel_date_before_valid_from(self) -> None:
        props = {
            "valid_from": "2012-03-31T02:03:00+00:00",
            "valid_until": "2012-04-05T00:00:00+00:00",
            "cancel_date": "2012-03-05T00:01:00+00:00",
        }
        enriched = _enrich_properties(props)
        assert enriched["cancel_date"] is None

    def test_enrich_clears_cancel_date_later_than_valid_until(self) -> None:
        props = {
            "valid_from": "2012-03-31T02:03:00+00:00",
            "valid_until": "2012-04-05T00:00:00+00:00",
            "cancel_date": "2012-04-06T00:00:00+00:00",
        }
        enriched = _enrich_properties(props)
        assert enriched["cancel_date"] is None

    def test_enrich_reconciles_cancel_date_from_self_cancel_text(self) -> None:
        props = {
            "valid_from": "2012-03-31T02:03:00+00:00",
            "valid_until": "2012-04-05T00:00:00+00:00",
            "cancel_date": "2012-03-05T00:01:00+00:00",
            "dtg": "2012-03-31T02:03:00",
            "year": 2012,
            "cancellations": [],
            "body": "3. CANCEL THIS MSG 05 APR.",
        }
        enriched = _enrich_properties(props)
        assert enriched["cancel_date"] == "2012-04-05T00:00:00+00:00"
        assert enriched["valid_until"] == "2012-04-05T00:00:00+00:00"

    def test_enrich_typo_self_cancel_gets_plus_24h_and_correction(self) -> None:
        props = {
            "valid_from": "2024-04-22T13:05:00+00:00",
            "dtg": "2024-04-22T13:05:00",
            "year": 2024,
            "cancellations": [],
            "body": "2. CANCEL THIS MSG 221259Z APR 24.",
            "corrections": [],
        }
        enriched = _enrich_properties(props)
        assert enriched["valid_until"] == "2024-04-23T13:05:00+00:00"
        assert enriched["cancel_date"] == "2024-04-23T13:05:00+00:00"
        assert any(
            c.get("code") == "self_cancel_before_valid_from"
            and c.get("before") == "2024-04-22T12:59:00+00:00"
            and c.get("after") == "2024-04-23T13:05:00+00:00"
            for c in enriched["corrections"]
        )


# ------------------------------------------------------------------
# collect_features
# ------------------------------------------------------------------


def _write_feature(
    path: Path,
    props: dict,
    geom: dict | None = None,
) -> None:
    """Helper: write a minimal GeoJSON Feature file."""
    feat = {
        "type": "Feature",
        "geometry": geom
        or {
            "type": "Point",
            "coordinates": [10.0, 60.0],
        },
        "properties": props,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(feat), encoding="utf-8")


class TestCollectFeatures:
    """Tests for collect_features."""

    def test_collects_single_feature(
        self,
        tmp_path: Path,
    ) -> None:
        _write_feature(
            tmp_path / "navwarns" / "A.json",
            {"dtg": "2025-01-10T00:00:00Z"},
        )
        feats = collect_features(tmp_path)
        assert len(feats) == 1
        assert feats[0]["properties"]["valid_from"] is not None

    def test_collects_feature_collection(
        self,
        tmp_path: Path,
    ) -> None:
        fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [0, 0],
                    },
                    "properties": {"year": 2020},
                },
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [1, 1],
                    },
                    "properties": {"year": 2020},
                },
            ],
        }
        out = tmp_path / "data.json"
        out.write_text(json.dumps(fc), encoding="utf-8")
        feats = collect_features(tmp_path)
        assert len(feats) == 2

    def test_skips_invalid_json(
        self,
        tmp_path: Path,
    ) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("NOT JSON", encoding="utf-8")
        feats = collect_features(tmp_path)
        assert feats == []

    def test_skips_non_geojson_dicts(
        self,
        tmp_path: Path,
    ) -> None:
        other = tmp_path / "other.json"
        other.write_text(
            json.dumps({"name": "test"}),
            encoding="utf-8",
        )
        feats = collect_features(tmp_path)
        assert feats == []

    def test_recursive_subdirectories(
        self,
        tmp_path: Path,
    ) -> None:
        """Features in nested dirs are collected."""
        _write_feature(
            tmp_path / "A" / "msg1.json",
            {"year": 2015},
        )
        _write_feature(
            tmp_path / "B" / "msg2.json",
            {"year": 2015},
        )
        _write_feature(
            tmp_path / "navwarns" / "msg3.json",
            {"year": 2015},
        )
        feats = collect_features(tmp_path)
        assert len(feats) == 3

    def test_enrichment_applied(
        self,
        tmp_path: Path,
    ) -> None:
        """Collected features get valid_from populated."""
        _write_feature(
            tmp_path / "f.json",
            {
                "dtg": "2023-06-15T12:00:00Z",
                "cancellations": [
                    "THIS MSG 200000Z JUN 23",
                ],
            },
        )
        feats = collect_features(tmp_path)
        p = feats[0]["properties"]
        assert p["valid_from"] is not None
        assert p["valid_until"] is not None


# ------------------------------------------------------------------
# build_archive
# ------------------------------------------------------------------


class TestBuildArchive:
    """Tests for build_archive."""

    def test_writes_geojson_file(
        self,
        tmp_path: Path,
    ) -> None:
        year_dir = tmp_path / "history" / "2023"
        out_dir = tmp_path / "docs"
        out_dir.mkdir()
        _write_feature(
            year_dir / "msg.json",
            {"year": 2023},
        )
        count = build_archive(2023, year_dir, out_dir)
        assert count == 1
        out_file = out_dir / "archive2023.geojson"
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 1

    def test_returns_zero_for_empty_dir(
        self,
        tmp_path: Path,
    ) -> None:
        year_dir = tmp_path / "empty"
        year_dir.mkdir()
        out_dir = tmp_path / "docs"
        out_dir.mkdir()
        count = build_archive(2099, year_dir, out_dir)
        assert count == 0
        # No file written for empty directory.
        assert not (out_dir / "archive2099.geojson").exists()


# ------------------------------------------------------------------
# build_manifest
# ------------------------------------------------------------------


class TestBuildManifest:
    """Tests for build_manifest."""

    def test_writes_manifest(self, tmp_path: Path) -> None:
        counts = {2020: 100, 2021: 200, 2022: 0}
        build_manifest(counts, tmp_path)
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        years = manifest["years"]
        # Year with 0 features is excluded.
        assert len(years) == 2
        assert years[0]["year"] == 2020
        assert years[0]["count"] == 100
        assert years[1]["year"] == 2021
        assert years[1]["count"] == 200

    def test_manifest_sorted(self, tmp_path: Path) -> None:
        counts = {2025: 10, 2010: 50, 2018: 30}
        build_manifest(counts, tmp_path)
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        y = [e["year"] for e in manifest["years"]]
        assert y == sorted(y)

    def test_manifest_merges_existing_archives(
        self,
        tmp_path: Path,
    ) -> None:
        """Rebuilding one year must not erase other years.

        Regression test: build_manifest previously only wrote the
        year_counts dict, so rebuilding 2021 alone would drop 2020.
        """
        # Pre-existing archive on disk for 2020 (3 features)
        fc_2020 = {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {}} for _ in range(3)],
        }
        (tmp_path / "archive2020.geojson").write_text(json.dumps(fc_2020))
        # Rebuild only 2021 with 5 features
        build_manifest({2021: 5}, tmp_path)
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        years = {e["year"]: e["count"] for e in manifest["years"]}
        assert years[2020] == 3, "existing archive year preserved"
        assert years[2021] == 5, "rebuilt year present"

    def test_manifest_rebuild_overrides_stale_disk(
        self,
        tmp_path: Path,
    ) -> None:
        """Freshly-built counts override stale on-disk archives."""
        fc_old = {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {}} for _ in range(99)],
        }
        (tmp_path / "archive2021.geojson").write_text(json.dumps(fc_old))
        # Rebuild 2021 with updated count
        build_manifest({2021: 7}, tmp_path)
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        years = {e["year"]: e["count"] for e in manifest["years"]}
        assert years[2021] == 7, "rebuilt count overrides disk"


# ------------------------------------------------------------------
# _scan_daily_presence / _apply_last_seen
# ------------------------------------------------------------------


class TestDailyPresenceInference:
    """Daily snapshot inference for disappearance-based cancellation."""

    def test_navtex_disappearance_detected(self, tmp_path: Path) -> None:
        year_dir = tmp_path / "2026"
        navtex_dir = year_dir / "NAVTEX_SE"
        navtex_dir.mkdir(parents=True)

        day1 = navtex_dir / "NAVTEX_SE_2026-01-01.html"
        day2 = navtex_dir / "NAVTEX_SE_2026-01-02.html"
        day1.write_text(
            "BALTIC SEA NAV WARN 001/26\n" "SWEDISH NAV WARN 002/26\n",
            encoding="utf-8",
        )
        day2.write_text(
            "SWEDISH NAV WARN 002/26\n",
            encoding="utf-8",
        )

        first_seen, last_seen = _scan_daily_presence(year_dir)

        assert first_seen["BALTIC SEA NAV WARN 001/26"] == "2026-01-01"
        assert last_seen["BALTIC SEA NAV WARN 001/26"] == "2026-01-01"
        assert "SWEDISH NAV WARN 002/26" not in last_seen

    def test_navtex_disappearance_detected_with_multiline_id(
        self,
        tmp_path: Path,
    ) -> None:
        """NAVTEX IDs split across lines in <b> blocks are detected."""
        year_dir = tmp_path / "2026"
        navtex_dir = year_dir / "NAVTEX_SE"
        navtex_dir.mkdir(parents=True)

        day1 = navtex_dir / "NAVTEX_SE_2026-05-03.html"
        day2 = navtex_dir / "NAVTEX_SE_2026-05-04.html"

        day1.write_text(
            "<b>\n"
            "    SWEDISH NAV WARN\n"
            "    078/26\n"
            "</b>\n"
            "<b>\n"
            "    BALTIC SEA NAV WARN\n"
            "    020/26\n"
            "</b>\n",
            encoding="utf-8",
        )
        day2.write_text(
            "<b>\n" "    BALTIC SEA NAV WARN\n" "    020/26\n" "</b>\n",
            encoding="utf-8",
        )

        first_seen, last_seen = _scan_daily_presence(year_dir)

        assert first_seen["SWEDISH NAV WARN 078/26"] == "2026-05-03"
        assert last_seen["SWEDISH NAV WARN 078/26"] == "2026-05-03"
        assert "BALTIC SEA NAV WARN 020/26" not in last_seen

    def test_andoya_disappearance_detected(self, tmp_path: Path) -> None:
        year_dir = tmp_path / "2026"
        andoya_dir = year_dir / "ANDOYA"
        andoya_dir.mkdir(parents=True)

        day1 = andoya_dir / "ANDOYA_2026-01-01.olx"
        day2 = andoya_dir / "ANDOYA_2026-01-02.olx"
        day1.write_text(
            "Rute ukjent\n"
            "4200.0 900.0 1 A\n"
            "4201.0 901.0 1 A\n"
            "4202.0 902.0 1 A\n"
            "MTekst 1: Navn: Danger Area One\n"
            "Rute ukjent\n"
            "4300.0 910.0 1 A\n"
            "4301.0 911.0 1 A\n"
            "4302.0 912.0 1 A\n"
            "MTekst 1: Navn: Danger Area Two\n",
            encoding="latin-1",
        )
        day2.write_text(
            "Rute ukjent\n"
            "4300.0 910.0 1 A\n"
            "4301.0 911.0 1 A\n"
            "4302.0 912.0 1 A\n"
            "MTekst 1: Navn: Danger Area Two\n",
            encoding="latin-1",
        )

        first_seen, last_seen = _scan_daily_presence(year_dir)

        assert first_seen["ANDOYA_Danger_Area_One"] == "2026-01-01"
        assert last_seen["ANDOYA_Danger_Area_One"] == "2026-01-01"
        assert "ANDOYA_Danger_Area_Two" not in last_seen

    def test_apply_last_seen_sets_valid_until(self) -> None:
        feature = {
            "type": "Feature",
            "id": "BALTIC SEA NAV WARN 001/26",
            "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
            "properties": {
                "msg_id": "BALTIC SEA NAV WARN 001/26",
                "valid_until": None,
            },
        }

        updated = _apply_last_seen(
            [feature],
            {"BALTIC SEA NAV WARN 001/26": "2026-01-01"},
        )

        assert updated == 1
        assert feature["properties"]["valid_until"] == "2026-01-01T23:59:59+00:00"

    def test_apply_last_seen_skips_until_before_valid_from(self) -> None:
        """Do not infer impossible intervals for recurring IDs."""
        feature = {
            "type": "Feature",
            "id": "ANDOYA_DANGER_AREA_ECHO",
            "geometry": None,
            "properties": {
                "msg_id": "ANDOYA_DANGER_AREA_ECHO",
                "valid_from": "2026-08-03T12:34:00+00:00",
                "valid_until": None,
            },
        }

        updated = _apply_last_seen(
            [feature],
            {"ANDOYA_DANGER_AREA_ECHO": "2026-05-12"},
        )

        assert updated == 0
        assert feature["properties"]["valid_until"] is None

    def test_build_archive_backfills_history_valid_until_from_navtex(
        self,
        tmp_path: Path,
    ) -> None:
        year_dir = tmp_path / "2026"
        navtex_dir = year_dir / "NAVTEX_SE"
        navwarn_dir = year_dir / "navwarns"
        out_dir = tmp_path / "docs"
        navtex_dir.mkdir(parents=True)
        navwarn_dir.mkdir(parents=True)
        out_dir.mkdir(parents=True)

        (navtex_dir / "NAVTEX_SE_2026-05-03.html").write_text(
            "<b>\n"
            "  SWEDISH NAV WARN\n"
            "  078/26\n"
            "</b>\n"
            "<b>\n"
            "  BALTIC SEA NAV WARN\n"
            "  020/26\n"
            "</b>\n",
            encoding="utf-8",
        )
        (navtex_dir / "NAVTEX_SE_2026-05-04.html").write_text(
            "<b>\n" "  BALTIC SEA NAV WARN\n" "  020/26\n" "</b>\n",
            encoding="utf-8",
        )

        stale_path = navwarn_dir / "SWEDISH_NAV_WARN_078_26.json"
        stale_path.write_text(
            json.dumps(
                {
                    "type": "Feature",
                    "id": "SWEDISH NAV WARN 078/26",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [23.291666666666668, 65.775],
                    },
                    "properties": {
                        "dtg": "2026-04-28T16:29:00",
                        "msg_id": "SWEDISH NAV WARN 078/26",
                        "year": 2026,
                        "cancellations": [],
                        "cancel_date": None,
                        "valid_from": "2026-04-28T16:29:00+00:00",
                        "valid_until": None,
                    },
                }
            ),
            encoding="utf-8",
        )

        count = build_archive(2026, year_dir, out_dir, extra_cancel_dirs=[])
        assert count == 1

        updated = json.loads(stale_path.read_text(encoding="utf-8"))
        assert updated["properties"]["valid_until"] == "2026-05-03T23:59:59+00:00"

    def test_build_archive_clears_invalid_until_when_inference_is_older(
        self,
        tmp_path: Path,
    ) -> None:
        """Existing impossible windows are cleared and not reintroduced."""
        year_dir = tmp_path / "2026"
        andoya_dir = year_dir / "ANDOYA"
        navwarn_dir = year_dir / "navwarns"
        out_dir = tmp_path / "docs"
        andoya_dir.mkdir(parents=True)
        navwarn_dir.mkdir(parents=True)
        out_dir.mkdir(parents=True)

        (andoya_dir / "ANDOYA_2026-05-12.olx").write_text(
            "Name: DANGER AREA ECHO\n",
            encoding="latin-1",
        )
        (andoya_dir / "ANDOYA_2026-08-06.olx").write_text(
            "Name: DANGER AREA SIERRA\n",
            encoding="latin-1",
        )

        stale_path = navwarn_dir / "ANDOYA_DANGER_AREA_ECHO.json"
        stale_path.write_text(
            json.dumps(
                {
                    "type": "Feature",
                    "id": "ANDOYA_DANGER_AREA_ECHO",
                    "geometry": None,
                    "properties": {
                        "dtg": "2026-08-03T12:34:00",
                        "msg_id": "ANDOYA_DANGER_AREA_ECHO",
                        "year": 2026,
                        "cancellations": [],
                        "valid_from": "2026-08-03T12:34:00+00:00",
                        "valid_until": "2026-05-12T23:59:59+00:00",
                    },
                }
            ),
            encoding="utf-8",
        )

        count = build_archive(2026, year_dir, out_dir, extra_cancel_dirs=[])
        assert count == 1

        updated = json.loads(stale_path.read_text(encoding="utf-8"))
        assert updated["properties"]["valid_until"] is None

    def test_build_archive_backfills_andoya_active_window_from_body(
        self,
        tmp_path: Path,
    ) -> None:
        """Persist Andoya body-derived valid_from/valid_until to history JSON."""
        year_dir = tmp_path / "2026"
        navwarn_dir = year_dir / "navwarns"
        out_dir = tmp_path / "docs"
        navwarn_dir.mkdir(parents=True)
        out_dir.mkdir(parents=True)

        stale_path = navwarn_dir / "ANDOYA_Fareomr_de_Studentrakett_And_ya_Space.json"
        stale_path.write_text(
            json.dumps(
                {
                    "type": "Feature",
                    "id": "ANDOYA_Fareomr_de_Studentrakett_And_ya_Space",
                    "geometry": None,
                    "properties": {
                        "dtg": "2026-08-03T12:34:00",
                        "msg_id": "ANDOYA_Fareomr_de_Studentrakett_And_ya_Space",
                        "year": 2026,
                        "cancellations": [],
                        "body": (
                            "Name: Danger Area ESC And\u00f8ya Space Description: "
                            "The danger area is active on July 30th with launch window "
                            "1245-1600 local time. Backup day is July 31st with launch "
                            "window 0845-1600 local time."
                        ),
                        "valid_from": "2026-08-03T12:34:00+00:00",
                        "valid_until": None,
                    },
                }
            ),
            encoding="utf-8",
        )

        count = build_archive(2026, year_dir, out_dir, extra_cancel_dirs=[])
        assert count == 1

        updated = json.loads(stale_path.read_text(encoding="utf-8"))
        assert updated["properties"]["valid_from"] == "2026-07-30T00:00:00+00:00"
        assert updated["properties"]["valid_until"] == "2026-07-31T23:59:59+00:00"

    def test_build_archive_backfills_from_to_period_without_last_seen(
        self,
        tmp_path: Path,
    ) -> None:
        """Persist computed FROM..TO validity to history files."""
        year_dir = tmp_path / "2026"
        navwarn_dir = year_dir / "navwarns"
        out_dir = tmp_path / "docs"
        navwarn_dir.mkdir(parents=True)
        out_dir.mkdir(parents=True)

        stale_path = navwarn_dir / "NAVAREA_XIX_79_26_grp1.json"
        stale_path.write_text(
            json.dumps(
                {
                    "type": "Feature",
                    "id": "NAVAREA XIX 79/26#grp1",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [0.0, 0.0],
                    },
                    "properties": {
                        "msg_id": "NAVAREA XIX 79/26#grp1",
                        "year": 2026,
                        "body": "ROCKET LAUNCH OPERATION FROM 15 TO 21 JUN 26.",
                        "valid_from": "2026-06-10T06:30:00+00:00",
                        "valid_until": None,
                    },
                }
            ),
            encoding="utf-8",
        )

        count = build_archive(2026, year_dir, out_dir, extra_cancel_dirs=[])
        assert count == 1

        updated = json.loads(stale_path.read_text(encoding="utf-8"))
        assert updated["properties"]["valid_until"] == "2026-06-21T00:00:00+00:00"

    def test_build_archive_persists_cancel_date_normalization(
        self,
        tmp_path: Path,
    ) -> None:
        """Invalid cancel_date is reconciled from self-cancel text."""
        year_dir = tmp_path / "2012"
        navwarn_dir = year_dir / "A"
        out_dir = tmp_path / "docs"
        navwarn_dir.mkdir(parents=True)
        out_dir.mkdir(parents=True)

        path = navwarn_dir / "HYDROARC_717_12_24_grp3.json"
        path.write_text(
            json.dumps(
                {
                    "type": "Feature",
                    "id": "HYDROARC 717/12(24)#grp3",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[-40.21, -0.03], [-40.21, -2.08]],
                    },
                    "properties": {
                        "msg_id": "HYDROARC 717/12(24)#grp3",
                        "dtg": "2012-03-31T02:03:00",
                        "year": 2012,
                        "body": "3. CANCEL THIS MSG 05 APR.",
                        "cancel_date": "2012-03-05T00:01:00+00:00",
                        "valid_from": "2012-03-31T02:03:00+00:00",
                        "valid_until": "2012-04-05T00:00:00+00:00",
                    },
                }
            ),
            encoding="utf-8",
        )

        count = build_archive(2012, year_dir, out_dir, extra_cancel_dirs=[])
        assert count == 1

        updated = json.loads(path.read_text(encoding="utf-8"))
        assert updated["properties"]["cancel_date"] == "2012-04-05T00:00:00+00:00"


# ------------------------------------------------------------------
# _resolve_cross_cancellations
# ------------------------------------------------------------------


def _make_feature(
    msg_id: str, valid_from: str, valid_until=None, cancellations=None
) -> dict:
    return {
        "type": "Feature",
        "id": msg_id,
        "geometry": {"type": "Point", "coordinates": [10.0, 60.0]},
        "properties": {
            "msg_id": msg_id,
            "valid_from": valid_from,
            "valid_until": valid_until,
            "cancellations": cancellations or [],
        },
    }


class TestResolveCrossCancellations:
    """_resolve_cross_cancellations sets valid_until on cancelled features."""

    def test_cross_cancel_tightens_valid_until(self) -> None:
        # Mirrors HYDROARC 60/26 cancelled by 61/26.
        cancelled = _make_feature(
            "HYDROARC 60/26",
            valid_from="2026-05-13T00:48:00+00:00",
            valid_until="2026-05-30T00:59:00+00:00",
            cancellations=["THIS MSG 300059Z MAY 26"],
        )
        canceller = _make_feature(
            "HYDROARC 61/26",
            valid_from="2026-05-13T01:28:00+00:00",
            valid_until="2026-05-30T00:59:00+00:00",
            cancellations=["HYDROARC 60/26", "THIS MSG 300059Z MAY 26"],
        )
        n = _resolve_cross_cancellations([cancelled, canceller])
        assert n == 1
        assert cancelled["properties"]["valid_until"] == "2026-05-13T01:28:00+00:00"

    def test_cross_cancel_does_not_extend_earlier_valid_until(self) -> None:
        # Cancelled feature already expires before the canceller was issued.
        cancelled = _make_feature(
            "HYDROARC 10/26",
            valid_from="2026-01-01T00:00:00+00:00",
            valid_until="2026-01-05T00:00:00+00:00",
        )
        canceller = _make_feature(
            "HYDROARC 11/26",
            valid_from="2026-01-10T00:00:00+00:00",
            cancellations=["HYDROARC 10/26"],
        )
        _resolve_cross_cancellations([cancelled, canceller])
        # valid_until must not be extended beyond the original earlier date.
        assert cancelled["properties"]["valid_until"] == "2026-01-05T00:00:00+00:00"

    def test_self_cancel_entries_are_ignored(self) -> None:
        # "THIS MSG ..." entries must never be used as cross-refs.
        feat = _make_feature(
            "HYDROARC 20/26",
            valid_from="2026-02-01T00:00:00+00:00",
            valid_until="2026-02-28T00:00:00+00:00",
        )
        canceller = _make_feature(
            "HYDROARC 21/26",
            valid_from="2026-02-10T00:00:00+00:00",
            cancellations=["THIS MSG 280000Z FEB 26"],
        )
        _resolve_cross_cancellations([feat, canceller])
        assert feat["properties"]["valid_until"] == "2026-02-28T00:00:00+00:00"

    def test_plain_num_year_ref_matches(self) -> None:
        # Plain "317/23" reference (from comma-separated multi-cancel list)
        # should still resolve via trailing-NNN/YY index.
        cancelled = _make_feature(
            "NAVAREA XIX 317/23",
            valid_from="2023-11-01T00:00:00+00:00",
            valid_until="2023-12-31T00:00:00+00:00",
        )
        canceller = _make_feature(
            "NAVAREA XIX 320/23",
            valid_from="2023-11-15T00:00:00+00:00",
            cancellations=["317/23"],
        )
        n = _resolve_cross_cancellations([cancelled, canceller])
        assert n == 1
        assert cancelled["properties"]["valid_until"] == "2023-11-15T00:00:00+00:00"

    def test_no_valid_from_on_canceller_skipped(self) -> None:
        cancelled = _make_feature(
            "HYDROARC 30/26",
            valid_from="2026-03-01T00:00:00+00:00",
            valid_until="2026-03-31T00:00:00+00:00",
        )
        canceller = _make_feature(
            "HYDROARC 31/26",
            valid_from=None,
            cancellations=["HYDROARC 30/26"],
        )
        canceller["properties"]["valid_from"] = None
        n = _resolve_cross_cancellations([cancelled, canceller])
        assert n == 0

    def test_returns_zero_when_nothing_to_resolve(self) -> None:
        feats = [
            _make_feature("HYDROARC 1/26", "2026-01-01T00:00:00+00:00"),
            _make_feature("HYDROARC 2/26", "2026-01-02T00:00:00+00:00"),
        ]
        assert _resolve_cross_cancellations(feats) == 0

    def test_group_feature_cancelled_by_num_year_ref(self) -> None:
        # Regression: NAVAREA XIX 90/26#grp2 must be cancelled by "90/26" ref.
        # The #grpN suffix is stripped when building by_num_year index.
        grp = _make_feature(
            "NAVAREA XIX 90/26#grp2",
            valid_from="2026-07-13T18:30:00+00:00",
        )
        canceller = _make_feature(
            "NAVAREA XIX 93/26",
            valid_from="2026-07-22T06:30:00+00:00",
            cancellations=["90/26"],
        )
        n = _resolve_cross_cancellations([grp, canceller])
        assert n == 1
        assert grp["properties"]["valid_until"] == "2026-07-22T06:30:00+00:00"


# ------------------------------------------------------------------
# build_archive — cross-scope cancellation (canceller still in current/)
# ------------------------------------------------------------------


class TestBuildArchiveCrossScope:
    """build_archive() applies cancellations from extra_cancel_dirs to archived features.

    Mirrors the real-world case where NAVAREA XIX 93/26 (in current/navwarns/)
    cancels 90/26 (in history/2026/navwarns/) before cleanup has moved 93/26
    to history.
    """

    def test_canceller_in_extra_dir_sets_valid_until(self, tmp_path: Path) -> None:
        history_dir = tmp_path / "history" / "navwarns"
        current_dir = tmp_path / "current" / "navwarns"
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        _write_feature(
            history_dir / "NAVAREA_XIX_90_26.json",
            {"msg_id": "NAVAREA XIX 90/26", "valid_from": "2026-07-13T18:30:00+00:00"},
        )
        _write_feature(
            history_dir / "NAVAREA_XIX_90_26_grp2.json",
            {
                "msg_id": "NAVAREA XIX 90/26#grp2",
                "valid_from": "2026-07-13T18:30:00+00:00",
            },
        )
        _write_feature(
            current_dir / "NAVAREA_XIX_93_26.json",
            {
                "msg_id": "NAVAREA XIX 93/26",
                "valid_from": "2026-07-22T06:30:00+00:00",
                "cancellations": ["90/26"],
            },
        )

        count = build_archive(
            2026, history_dir, output_dir, extra_cancel_dirs=[current_dir]
        )

        # Only the 2 history features are written to the archive
        assert count == 2
        archive = json.loads((output_dir / "archive2026.geojson").read_text())
        ids = {f.get("properties", {}).get("msg_id") for f in archive["features"]}
        assert "NAVAREA XIX 93/26" not in ids

        # Both 90/26 features must have valid_until set to the canceller's valid_from
        by_mid = {f["properties"]["msg_id"]: f for f in archive["features"]}
        assert (
            by_mid["NAVAREA XIX 90/26"]["properties"]["valid_until"]
            == "2026-07-22T06:30:00+00:00"
        )
        assert (
            by_mid["NAVAREA XIX 90/26#grp2"]["properties"]["valid_until"]
            == "2026-07-22T06:30:00+00:00"
        )
