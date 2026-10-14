"""Regression tests for nightly stale-warning cleanup."""

import json
from pathlib import Path

import pytest

from scripts.cleanup import remove_stale_no_expiry


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        "snapshot",
        {"type": "FeatureCollection", "features": [None]},
        {"type": "FeatureCollection", "features": {}},
        {"type": "FeatureCollection", "features": "invalid"},
        {"type": "Feature", "properties": ["invalid"]},
        {"type": "Feature", "properties": None},
        {"type": "Feature", "properties": {"valid_from": 123}},
        {"type": "Feature", "properties": {"valid_from": "invalid"}},
    ],
)
def test_cleanup_skips_invalid_records_and_moves_stale_warning(
    tmp_path: Path,
    payload: object,
) -> None:
    current_dir = tmp_path / "current"
    history_dir = tmp_path / "history"
    current_dir.mkdir()
    invalid_path = current_dir / "invalid.json"
    original = json.dumps(payload)
    invalid_path.write_text(original, encoding="utf-8")
    stale_path = current_dir / "NAVAREA_XIX_1_20.json"
    stale_path.write_text(
        json.dumps(
            {
                "type": "Feature",
                "properties": {"valid_from": "2020-01-01T00:00:00Z"},
            }
        ),
        encoding="utf-8",
    )

    moved = remove_stale_no_expiry([current_dir], history_dir_base=history_dir)

    assert moved == [stale_path]
    assert invalid_path.read_text(encoding="utf-8") == original
    assert (history_dir / "2020" / "navwarns" / stale_path.name).exists()
