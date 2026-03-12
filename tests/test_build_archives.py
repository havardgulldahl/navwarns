"""Tests for scripts/build_archives.py.

Covers property enrichment, feature collection, archive
building, and manifest generation using temporary directories.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.build_archives import (
    _compute_valid_from,
    _compute_valid_until,
    _enrich_properties,
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
        """Only parses entries mentioning THIS MSG."""
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
