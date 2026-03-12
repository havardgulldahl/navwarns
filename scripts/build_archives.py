"""Build archive GeoJSON files from history/ directory.

Walks ``history/<year>/`` (both old layout with A/B/C/D/E sub-dirs
and new layout with navwarns/prips/NAVAREAXX) and merges all
individual Feature JSON files into per-year FeatureCollections
written to ``docs/archive<year>.geojson``.

Also writes ``docs/manifest.json`` listing available years with
feature counts so the frontend can discover them dynamically.

Usage::

    python scripts/build_archives.py          # all years
    python scripts/build_archives.py 2024     # single year
    python scripts/build_archives.py 2024 2025
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = ROOT / "history"
DOCS_DIR = ROOT / "docs"

MONTH_MAP = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


def _compute_valid_from(props: Dict[str, Any]) -> Optional[str]:
    """Derive valid_from from dtg or year."""
    dtg = props.get("dtg")
    if dtg:
        if isinstance(dtg, str):
            try:
                dt = datetime.fromisoformat(dtg.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.isoformat()
            except ValueError:
                pass
        return dtg if isinstance(dtg, str) else None
    year = props.get("year")
    if year:
        try:
            return datetime(int(year), 1, 1, tzinfo=timezone.utc).isoformat()
        except (ValueError, TypeError):
            pass
    return None


def _compute_valid_until(
    props: Dict[str, Any],
) -> Optional[str]:
    """Parse self-cancellation dates from cancellations list."""
    cancellations = props.get("cancellations") or []
    for cancel in cancellations:
        if not cancel:
            continue
        upper = cancel.upper()
        if "THIS MSG" not in upper and "THIS MESSAGE" not in upper:
            continue
        m = re.match(
            r"THIS (?:MSG|MESSAGE) (\d{2})(\d{2})(\d{2})"
            r"(?:Z| UTC) ([A-Z]{3}) (\d{2})",
            cancel,
        )
        if m:
            day, hour, minute = (
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
            )
            mon = MONTH_MAP.get(m.group(4))
            yr = 2000 + int(m.group(5))
            if mon:
                try:
                    return datetime(
                        yr,
                        mon,
                        day,
                        hour,
                        minute,
                        tzinfo=timezone.utc,
                    ).isoformat()
                except ValueError:
                    pass
        m2 = re.match(
            r"THIS (?:MSG|MESSAGE) (\d{2}) ([A-Z]{3})" r" (\d{2})",
            cancel,
        )
        if m2:
            day = int(m2.group(1))
            mon = MONTH_MAP.get(m2.group(2))
            yr = 2000 + int(m2.group(3))
            if mon:
                try:
                    return datetime(
                        yr,
                        mon,
                        day,
                        tzinfo=timezone.utc,
                    ).isoformat()
                except ValueError:
                    pass
    return None


def _enrich_properties(
    props: Dict[str, Any],
) -> Dict[str, Any]:
    """Add valid_from / valid_until if missing."""
    if "valid_from" not in props or props["valid_from"] is None:
        props["valid_from"] = _compute_valid_from(props)
    if "valid_until" not in props or props["valid_until"] is None:
        props["valid_until"] = _compute_valid_until(props)
    return props


def _infer_year_from_dir(
    year_dir: Path,
) -> Optional[int]:
    """Parse year integer from directory name."""
    try:
        return int(year_dir.name)
    except ValueError:
        return None


def collect_features(year_dir: Path) -> List[Dict[str, Any]]:
    """Collect all GeoJSON Features from a year directory."""
    features: List[Dict[str, Any]] = []
    json_files = sorted(year_dir.rglob("*.json"))
    for path in json_files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            print(f"  SKIP (bad JSON): {path}", file=sys.stderr)
            continue

        if not isinstance(data, dict):
            continue

        feat_type = data.get("type")
        if feat_type == "Feature":
            data["properties"] = _enrich_properties(data.get("properties") or {})
            features.append(data)
        elif feat_type == "FeatureCollection":
            for feat in data.get("features") or []:
                if isinstance(feat, dict):
                    feat["properties"] = _enrich_properties(
                        feat.get("properties") or {}
                    )
                    features.append(feat)
    return features


def build_archive(
    year: int,
    year_dir: Path,
    output_dir: Path,
) -> int:
    """Build a single archive file. Returns feature count."""
    features = collect_features(year_dir)
    if not features:
        print(f"  {year}: no features found, skipping")
        return 0

    collection = {
        "type": "FeatureCollection",
        "features": features,
    }
    out_path = output_dir / f"archive{year}.geojson"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(collection, f, ensure_ascii=False)
    print(f"  {year}: {len(features)} features -> {out_path}")
    return len(features)


def build_manifest(
    year_counts: Dict[int, int],
    output_dir: Path,
) -> None:
    """Write manifest.json with available years and counts."""
    manifest = {
        "years": [
            {"year": yr, "count": cnt}
            for yr, cnt in sorted(year_counts.items())
            if cnt > 0
        ],
    }
    out_path = output_dir / "manifest.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"  manifest -> {out_path}")


def main(years: Optional[List[int]] = None) -> None:
    """Build archives for specified years (or all)."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    if years:
        year_dirs = [(yr, HISTORY_DIR / str(yr)) for yr in years]
    else:
        year_dirs = [
            (int(d.name), d)
            for d in sorted(HISTORY_DIR.iterdir())
            if d.is_dir() and d.name.isdigit()
        ]

    year_counts: Dict[int, int] = {}
    for yr, yr_dir in year_dirs:
        if not yr_dir.is_dir():
            print(f"  {yr}: directory not found, skip")
            continue
        count = build_archive(yr, yr_dir, DOCS_DIR)
        year_counts[yr] = count

    build_manifest(year_counts, DOCS_DIR)
    total = sum(year_counts.values())
    print(f"Done: {len(year_counts)} years," f" {total} total features")


if __name__ == "__main__":
    requested = [int(a) for a in sys.argv[1:] if a.isdigit()]
    main(requested or None)
