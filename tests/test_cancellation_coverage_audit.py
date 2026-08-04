"""Regression tests for archive cancellation coverage audit."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.cancellation_coverage_audit import find_self_cancel_regex_misses

ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = ROOT / "history"
BASELINE_PATH = ROOT / "tests" / "data" / "cancellation_coverage_baseline.json"


def test_cancellation_coverage_misses_do_not_increase() -> None:
    """High-confidence self-cancel misses must not exceed baseline."""
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    years = baseline["years"]
    report = find_self_cancel_regex_misses(
        history_dir=HISTORY_DIR,
        years=years,
        max_examples_per_family=2,
    )

    assert report["candidate_lines"] > 0
    assert report["miss_count"] <= baseline["miss_count"]

    baseline_families = baseline["misses_by_family"]
    report_families = report["misses_by_family"]
    for family, count in report_families.items():
        assert count <= baseline_families.get(family, 0)


def test_bare_dtg_miss_family_absent_in_baseline_years() -> None:
    """Known bare-DTG misses are expected to remain fixed."""
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    report = find_self_cancel_regex_misses(
        history_dir=HISTORY_DIR,
        years=baseline["years"],
        max_examples_per_family=2,
    )

    families = report["misses_by_family"]
    assert families.get("bare_dtg_with_year", 0) == 0
    assert families.get("bare_dtg_without_year", 0) == 0
