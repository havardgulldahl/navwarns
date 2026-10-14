"""Exercise nightly shell steps without scraping or publishing."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


def _steps() -> list[dict]:
    workflow = Path(__file__).parents[1] / ".github/workflows/scrape.yml"
    return yaml.safe_load(workflow.read_text())["jobs"]["scrape"]["steps"]


def test_scrapers_are_best_effort() -> None:
    scrapers = [
        step
        for step in _steps()
        if step.get("run", "").startswith("python scripts/scraper")
    ]
    assert scrapers
    assert all(step.get("continue-on-error") is True for step in scrapers)


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is unavailable")
def test_merge_skips_unsupported_json_shapes(tmp_path: Path) -> None:
    navwarns = tmp_path / "current/navwarns"
    navwarns.mkdir(parents=True)
    (tmp_path / "current/prips").mkdir()
    valid = {"type": "Feature", "id": "valid", "properties": {}}
    payloads = [
        valid,
        None,
        123,
        {"type": "FeatureCollection", "features": 123},
        {"type": "Feature", "properties": ["invalid"]},
        {
            "type": "FeatureCollection",
            "features": [valid, None, {"warningnumber": "1/26"}],
        },
        {
            "type": "Feature",
            "id": "bad-date",
            "properties": {"valid_until": 123},
        },
    ]
    for index, payload in enumerate(payloads):
        (navwarns / f"{index}.json").write_text(
            json.dumps(payload) + "\n", encoding="utf-8"
        )
    step = next(
        step
        for step in _steps()
        if step.get("name", "").startswith("Merge individual features")
    )

    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    merged = json.loads((tmp_path / "current/navwarns.geojson").read_text())
    assert merged["features"] == [valid]


@pytest.mark.parametrize("has_current", [False, True])
def test_publish_handles_missing_optional_files(
    tmp_path: Path, has_current: bool
) -> None:
    (tmp_path / "current").mkdir()
    (tmp_path / "history").mkdir()
    (tmp_path / "docs").mkdir()
    timestamp = "2026-10-14T00:00:00+00:00\n"
    (tmp_path / "current/.scrape_timestamp_PRIP").write_text(timestamp)
    if has_current:
        (tmp_path / "current/navwarns.geojson").write_text("{}")
    step = next(step for step in _steps() if step.get("name") == ("Commit and push"))
    script = "git() { return 0; }\n" + step["run"]

    result = subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "GITHUB_TOKEN": "",
            "GITHUB_REPOSITORY": "test/test",
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "docs/scrape_timestamp_PRIP.txt").read_text() == timestamp
    assert not (tmp_path / "docs/scrape_timestamp_NAVCO.txt").exists()
    assert (tmp_path / "docs/navwarns.geojson").exists() is has_current
