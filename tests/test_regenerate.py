"""Tests for scripts/regenerate.py.

Covers the routing logic in regenerate_history that directs
PRIP files to regenerate_prip_file and navwarn files to
regenerate_navwarn_file, and verifies that PRIP files are
never subjected to the navwarn handler's deletion logic.
"""

import json
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest

from scripts.regenerate import (
    rebuild_current_geojson,
    regenerate_all,
    regenerate_history,
    regenerate_navwarn_file,
    regenerate_prip_file,
)

# -- helpers -----------------------------------------------------------


def _minimal_prip_feature(msg_id: str = "PRIP ARKHANGELSK 1/25") -> dict:
    """Return a minimal valid PRIP GeoJSON Feature."""
    return {
        "type": "Feature",
        "id": msg_id,
        "geometry": {
            "type": "Point",
            "coordinates": [40.0, 65.0],
        },
        "properties": {
            "dtg": "2025-01-10T00:00:00",
            "raw_dtg": "ПРИП АРХАНГЕЛЬСК 1/25",
            "msg_id": msg_id,
            "year": 2025,
            "cancellations": [],
            "hazard_type": "hazardous operations",
            "geometry_kind": "point",
            "radius_nm": None,
            "body": "1. СТРЕЛЬБЫ\n2. ОТМ=",
            "cancel_date": None,
            "valid_from": None,
            "valid_until": None,
            "summary": None,
        },
    }


def _minimal_navwarn_feature(
    msg_id: str = "NAVAREA XX 1/25",
) -> dict:
    """Return a minimal valid navwarn GeoJSON Feature."""
    return {
        "type": "Feature",
        "id": msg_id,
        "geometry": {
            "type": "Point",
            "coordinates": [100.0, 70.0],
        },
        "properties": {
            "dtg": "2025-03-01T00:00:00",
            "raw_dtg": "010000 UTC MAR 25",
            "msg_id": msg_id,
            "year": 2025,
            "cancellations": [],
            "hazard_type": "hazardous operations",
            "geometry_kind": "point",
            "radius_nm": None,
            "body": "NAVAREA XX 1/25\nHAZARDOUS OPS",
            "cancel_date": None,
            "valid_from": None,
            "valid_until": None,
            "summary": None,
        },
    }


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


# ------------------------------------------------------------------
# regenerate_history routing
# ------------------------------------------------------------------


class TestRegenerateHistoryRouting:
    """Verify PRIP vs navwarn routing in regenerate_history.

    Regression: before the fix, all history files were routed
    through regenerate_navwarn_file — including PRIPs.  The
    navwarn handler has deletion logic for expanding groups,
    which could destroy PRIP files that have no HTML source.
    """

    def test_prip_in_prips_dir_uses_prip_handler(
        self,
        tmp_path: Path,
    ) -> None:
        """PRIP file under history/<year>/prips/ -> prip handler."""
        prip_dir = tmp_path / "2025" / "prips"
        prip_path = prip_dir / "PRIP_ARKHANGELSK_1_25.json"
        _write_json(prip_path, _minimal_prip_feature())

        with (
            patch("scripts.regenerate.HISTORY_DIR", tmp_path),
            patch(
                "scripts.regenerate.regenerate_prip_file",
                return_value=[prip_path],
            ) as mock_prip,
            patch(
                "scripts.regenerate.regenerate_navwarn_file",
                return_value=[],
            ) as mock_nw,
        ):
            regenerate_history(dry_run=False)

        mock_prip.assert_called_once()
        mock_nw.assert_not_called()

    def test_prip_prefix_in_navwarns_dir_uses_prip_handler(
        self,
        tmp_path: Path,
    ) -> None:
        """PRIP_ prefixed file under navwarns/ -> prip handler."""
        nw_dir = tmp_path / "2025" / "navwarns"
        prip_path = nw_dir / "PRIP_WEST_5_25.json"
        _write_json(prip_path, _minimal_prip_feature("PRIP WEST 5/25"))

        with (
            patch("scripts.regenerate.HISTORY_DIR", tmp_path),
            patch(
                "scripts.regenerate.regenerate_prip_file",
                return_value=[prip_path],
            ) as mock_prip,
            patch(
                "scripts.regenerate.regenerate_navwarn_file",
                return_value=[],
            ) as mock_nw,
        ):
            regenerate_history(dry_run=False)

        mock_prip.assert_called_once()
        mock_nw.assert_not_called()

    def test_navwarn_uses_navwarn_handler(
        self,
        tmp_path: Path,
    ) -> None:
        """Regular navwarn -> navwarn handler (not prip)."""
        nw_dir = tmp_path / "2025" / "navwarns"
        nw_path = nw_dir / "NAVAREA_XX_1_25.json"
        _write_json(nw_path, _minimal_navwarn_feature())

        with (
            patch("scripts.regenerate.HISTORY_DIR", tmp_path),
            patch(
                "scripts.regenerate.regenerate_prip_file",
                return_value=[],
            ) as mock_prip,
            patch(
                "scripts.regenerate.regenerate_navwarn_file",
                return_value=[nw_path],
            ) as mock_nw,
        ):
            regenerate_history(dry_run=False)

        mock_nw.assert_called_once()
        mock_prip.assert_not_called()

    def test_mixed_files_routed_correctly(
        self,
        tmp_path: Path,
    ) -> None:
        """Both PRIP and navwarn files: each to its handler."""
        prip_dir = tmp_path / "2025" / "prips"
        nw_dir = tmp_path / "2025" / "navwarns"
        prip_path = prip_dir / "PRIP_ARKHANGELSK_1_25.json"
        nw_path = nw_dir / "NAVAREA_XX_1_25.json"
        _write_json(prip_path, _minimal_prip_feature())
        _write_json(nw_path, _minimal_navwarn_feature())

        with (
            patch("scripts.regenerate.HISTORY_DIR", tmp_path),
            patch(
                "scripts.regenerate.regenerate_prip_file",
                return_value=[prip_path],
            ) as mock_prip,
            patch(
                "scripts.regenerate.regenerate_navwarn_file",
                return_value=[nw_path],
            ) as mock_nw,
        ):
            stats = regenerate_history(dry_run=False)

        mock_prip.assert_called_once()
        mock_nw.assert_called_once()
        assert stats["history_written"] == 2


# ------------------------------------------------------------------
# PRIP files survive regeneration (no deletion)
# ------------------------------------------------------------------


class TestPripFilesNotDeleted:
    """Ensure PRIP files in history are never deleted.

    The navwarn handler deletes original files when re-parsing
    splits them into group files.  PRIP files must not go through
    that code path.
    """

    def test_prip_file_survives_regenerate_prip(
        self,
        tmp_path: Path,
    ) -> None:
        """regenerate_prip_file never deletes the source file."""
        prip_dir = tmp_path / "prips"
        prip_dir.mkdir(parents=True)
        prip_path = prip_dir / "PRIP_ARKHANGELSK_1_25.json"
        _write_json(prip_path, _minimal_prip_feature())

        result = regenerate_prip_file(prip_path, prip_dir, dry_run=False)
        assert prip_path.exists(), "PRIP file must not be deleted"
        assert len(result) >= 1


# ------------------------------------------------------------------
# current/navwarns.geojson rebuild
# ------------------------------------------------------------------


class TestRebuildCurrentGeojson:
    """Verify rebuilding of current/navwarns.geojson from source features."""

    def test_rebuild_current_geojson_writes_feature_collection(
        self,
        tmp_path: Path,
    ) -> None:
        """Rebuild includes both navwarn and PRIP features."""
        current_dir = tmp_path / "current"
        navwarns_dir = current_dir / "navwarns"
        prips_dir = current_dir / "prips"
        navwarns_dir.mkdir(parents=True)
        prips_dir.mkdir(parents=True)

        nw_feat = _minimal_navwarn_feature("NAVAREA XX 9/25")
        prip_feat = _minimal_prip_feature("PRIP WEST 8/25")
        _write_json(navwarns_dir / "NAVAREA_XX_9_25.json", nw_feat)
        _write_json(prips_dir / "PRIP_WEST_8_25.json", prip_feat)

        out_path = current_dir / "navwarns.geojson"
        with (
            patch("scripts.regenerate.CURRENT_DIR", current_dir),
            patch("scripts.regenerate.NAVWARNS_DIR", navwarns_dir),
            patch("scripts.regenerate.PRIPS_DIR", prips_dir),
            patch("scripts.regenerate.CURRENT_GEOJSON_PATH", out_path),
        ):
            count = rebuild_current_geojson(dry_run=False)

        assert count == 2
        assert out_path.exists()
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 2
        ids = {f.get("id") for f in data["features"]}
        assert ids == {"NAVAREA XX 9/25", "PRIP WEST 8/25"}

    def test_regenerate_all_invokes_rebuild_current_geojson(
        self,
        tmp_path: Path,
    ) -> None:
        """Full regeneration should rebuild current/navwarns.geojson."""
        missing_navwarns = tmp_path / "missing-navwarns"
        missing_prips = tmp_path / "missing-prips"
        with (
            patch("scripts.regenerate.regenerate_history", return_value={}),
            patch("scripts.regenerate.rebuild_current_geojson") as mock_rebuild,
            patch("scripts.regenerate.build_archives.main"),
            patch("scripts.regenerate.NAVWARNS_DIR", missing_navwarns),
            patch("scripts.regenerate.PRIPS_DIR", missing_prips),
        ):
            regenerate_all(dry_run=False)

        mock_rebuild.assert_called_once_with(dry_run=False)
