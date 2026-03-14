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
    regenerate_history,
    regenerate_navwarn_file,
    regenerate_prip_file,
    HISTORY_DIR,
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
