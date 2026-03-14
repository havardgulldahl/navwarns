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
    """Parse self-cancellation dates from cancellations list and body text."""
    # Search both cancellations list and body text for self-cancel patterns
    sources = list(props.get("cancellations") or [])
    body = props.get("body") or ""
    if body:
        for line in re.split(r"[.\n]", body.upper()):
            if "THIS MSG" in line or "THIS MESSAGE" in line:
                sources.append(line.strip())

    for cancel in sources:
        if not cancel:
            continue
        upper = cancel.upper()
        if "THIS MSG" not in upper and "THIS MESSAGE" not in upper:
            continue
        # Full DTG: DDHHMM[Z| UTC| ] MON YY
        m = re.search(
            r"THIS (?:MSG|MESSAGE) (\d{2})(\d{2})(\d{2})"
            r"(?:Z| UTC)? ?([A-Z]{3}) (\d{2})",
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
        m2 = re.search(
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


def _dedup_key(feat: Dict[str, Any]) -> str:
    """Return a deduplication key for a feature.

    Uses feature id (includes group suffix like #grp1),
    then msg_id, then body+geometry hash as fallback.
    """
    fid = feat.get("id")
    if fid:
        return f"fid:{fid}"
    props = feat.get("properties") or {}
    mid = props.get("msg_id")
    if mid:
        return f"id:{mid}"
    # Fallback: body text + geometry type to distinguish
    # features at different locations with different geometry
    body = (props.get("body") or "").strip()[:150]
    geom = feat.get("geometry") or {}
    geom_key = str(geom.get("coordinates", ""))[:60]
    return f"body:{body}|geo:{geom_key}"


def _deduplicate_features(
    features: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Remove duplicate features, keeping the best date coverage.

    Daily scrapes of the same warning produce near-identical
    features.  Keep the copy with the earliest valid_from and
    latest valid_until so the timeline filter is most accurate.
    """
    best: Dict[str, Dict[str, Any]] = {}
    for feat in features:
        key = _dedup_key(feat)
        if key not in best:
            best[key] = feat
            continue
        # Merge: keep earliest valid_from, latest valid_until
        old_p = best[key].get("properties") or {}
        new_p = feat.get("properties") or {}
        old_from = old_p.get("valid_from")
        new_from = new_p.get("valid_from")
        if new_from and (not old_from or new_from < old_from):
            old_p["valid_from"] = new_from
        old_until = old_p.get("valid_until")
        new_until = new_p.get("valid_until")
        if new_until and (not old_until or new_until > old_until):
            old_p["valid_until"] = new_until
    return list(best.values())


def _scan_daily_presence(
    year_dir: Path,
) -> Dict[str, str]:
    """Scan daily scrape snapshots to find last-seen dates.

    Returns a mapping of msg_id -> last date (ISO string) the
    message appeared in a daily scrape.  Works for NAVAREAXX
    (navwarns_raw.txt) and PRIP (HTML files).
    """
    RU_MAP = {
        "АРХАНГЕЛЬСК": "ARKHANGELSK",
        "МУРМАНСК": "MURMANSK",
        "ЗАПАД": "WEST",
    }
    last_seen: Dict[str, str] = {}
    all_dates: List[str] = []

    # NAVAREAXX: dated subdirectories with navwarns_raw.txt
    nxx_dir = year_dir / "NAVAREAXX"
    if nxx_dir.is_dir():
        for d in nxx_dir.iterdir():
            if not d.is_dir():
                continue
            date_str = d.name  # e.g. 2025-09-23
            raw = d / "navwarns_raw.txt"
            if not raw.exists():
                continue
            all_dates.append(date_str)
            text = raw.read_text(errors="replace")
            for m in re.finditer(r"NAVAREA XX (\d+/\d+)", text):
                mid = f"NAVAREA XX {m.group(1)}"
                if mid not in last_seen or date_str > last_seen[mid]:
                    last_seen[mid] = date_str

    # PRIP: HTML files with date in filename
    prip_dir = year_dir / "PRIP"
    if prip_dir.is_dir():
        for html_file in prip_dir.glob("*.html"):
            dm = re.search(r"(\d{4}-\d{2}-\d{2})", html_file.name)
            if not dm:
                continue
            date_str = dm.group(1)
            all_dates.append(date_str)
            text = html_file.read_text(errors="replace")
            for m in re.finditer(
                r"ПРИП\s+(АРХАНГЕЛЬСК|МУРМАНСК|ЗАПАД)" r"\s+(\d+)/(\d+)",
                text,
            ):
                reg = RU_MAP.get(m.group(1), m.group(1))
                ref = f"PRIP {reg} {m.group(2)}/{m.group(3)}"
                if ref not in last_seen or date_str > last_seen[ref]:
                    last_seen[ref] = date_str

    if not all_dates:
        return {}

    # Only use last_seen as valid_until when the message disappeared
    # *before* the final scrape date (otherwise it may still be active)
    final_date = max(all_dates)
    return {mid: date for mid, date in last_seen.items() if date < final_date}


def _apply_last_seen(
    features: List[Dict[str, Any]],
    last_seen: Dict[str, str],
) -> int:
    """Set valid_until from last-seen dates for features that lack one.

    Returns count of features updated.
    """
    updated = 0
    for feat in features:
        props = feat.get("properties") or {}
        if props.get("valid_until"):
            continue
        # Match by msg_id or feature id (without group suffix)
        mid = props.get("msg_id") or ""
        fid = feat.get("id") or ""
        # For grouped features like "PRIP WEST 87/25#grp3",
        # strip the group suffix to match the parent msg_id
        base_id = re.sub(r"#grp\d+$", "", fid)
        date = last_seen.get(mid) or last_seen.get(base_id)
        if date:
            props["valid_until"] = f"{date}T23:59:59+00:00"
            updated += 1
    return updated


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

    before = len(features)
    features = _deduplicate_features(features)
    if before != len(features):
        print(f"  {year}: deduplicated {before} -> {len(features)}")

    # Infer valid_until from daily scrape disappearance
    last_seen = _scan_daily_presence(year_dir)
    if last_seen:
        n = _apply_last_seen(features, last_seen)
        if n:
            print(
                f"  {year}: inferred valid_until for {n} features" " from daily scrapes"
            )

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
    """Write manifest.json with available years and counts.

    Merges *year_counts* with any existing archive files on disk so
    that rebuilding a single year does not erase other years from the
    manifest.
    """
    # Discover all archiveYYYY.geojson files already on disk
    existing: Dict[int, int] = {}
    for archive_path in output_dir.glob("archive*.geojson"):
        m = re.search(r"archive(\d{4})\.geojson$", archive_path.name)
        if not m:
            continue
        yr = int(m.group(1))
        try:
            with open(archive_path, encoding="utf-8") as f:
                data = json.load(f)
            cnt = len(data.get("features") or [])
        except (json.JSONDecodeError, OSError):
            cnt = 0
        if cnt > 0:
            existing[yr] = cnt

    # Merge: rebuilt years override, existing years are preserved
    merged = {**existing, **year_counts}

    manifest = {
        "years": [
            {"year": yr, "count": cnt} for yr, cnt in sorted(merged.items()) if cnt > 0
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
