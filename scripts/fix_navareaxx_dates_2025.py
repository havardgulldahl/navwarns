"""Fix valid_from / valid_until for archived NAVAREA XX messages.

The Rosatom scraper does not embed DTG timestamps so the parser falls
back to YYYY-01-01.  This script scans the daily raw-text snapshots in
``history/<year>/NAVAREAXX/<date>/navwarns_raw.txt`` across all
available years to determine when each NAVAREA XX ID first appeared and
when it was last seen, then patches the JSON files in
``history/<year>/navwarns/`` accordingly.

Rules applied:
  valid_from  — set to first-seen snapshot date (skipped if no record).
  valid_until — set to last-seen date + end of day ONLY when:
                  (a) the JSON has no valid_until already (i.e. no
                      explicit self-cancel date was parsed from the
                      message body), AND
                  (b) the message disappeared before the latest
                      snapshot date (indicating it expired).

Usage::

    python scripts/fix_navareaxx_dates_2025.py           # dry-run, all years
    python scripts/fix_navareaxx_dates_2025.py --write   # apply all years
    python scripts/fix_navareaxx_dates_2025.py --year 25 --write
    python scripts/fix_navareaxx_dates_2025.py --year 26 --write
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]

# Regex for NAVAREA XX <num>/<yr> in raw text or HTML
NAVXX_RE = re.compile(r"NAVAREA XX (\d+/\d+)")


def scan_all_snapshots() -> Tuple[Dict[str, str], Dict[str, str], str]:
    """Scan every available NAVAREAXX snapshot directory across all years.

    Returns (first_seen, last_seen, final_date) keyed by full ID like
    ``NAVAREA XX 103/25``.  final_date is the latest snapshot date seen.
    """
    first_seen: Dict[str, str] = {}
    last_seen: Dict[str, str] = {}
    all_dates: Set[str] = set()

    for year_dir in sorted((ROOT / "history").iterdir()):
        nxx_dir = year_dir / "NAVAREAXX"
        if not nxx_dir.is_dir():
            continue

        for child in sorted(nxx_dir.iterdir()):
            if child.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}", child.name):
                date_str = child.name
                raw = child / "navwarns_raw.txt"
                if raw.exists():
                    all_dates.add(date_str)
                    for m in NAVXX_RE.finditer(raw.read_text(errors="replace")):
                        nid = f"NAVAREA XX {m.group(1)}"
                        if nid not in first_seen or date_str < first_seen[nid]:
                            first_seen[nid] = date_str
                        if nid not in last_seen or date_str > last_seen[nid]:
                            last_seen[nid] = date_str

            elif child.is_file():
                dm = re.search(r"(\d{4}-\d{2}-\d{2})", child.name)
                if dm and child.suffix == ".html":
                    date_str = dm.group(1)
                    all_dates.add(date_str)
                    for m in NAVXX_RE.finditer(child.read_text(errors="replace")):
                        nid = f"NAVAREA XX {m.group(1)}"
                        if nid not in first_seen or date_str < first_seen[nid]:
                            first_seen[nid] = date_str
                        if nid not in last_seen or date_str > last_seen[nid]:
                            last_seen[nid] = date_str

    final_date = max(all_dates) if all_dates else ""
    return first_seen, last_seen, final_date


def navxx_id_from_json(data: dict) -> Optional[str]:
    """Extract the canonical NAVAREA XX ID from a JSON feature."""
    props = data.get("properties") or {}
    mid = props.get("msg_id")
    if mid:
        return mid
    pid = props.get("parent_id")
    if pid:
        return pid
    fid = data.get("id") or ""
    return re.sub(r"#grp\d+$", "", fid) or None


def fix_year(
    year_suffix: str,
    first_seen: Dict[str, str],
    last_seen: Dict[str, str],
    final_date: str,
    write: bool,
) -> None:
    navwarn_dir = ROOT / "history" / f"20{year_suffix}" / "navwarns"
    if not navwarn_dir.exists():
        print(f"  [20{year_suffix}] No navwarns dir found, skipping.")
        return

    json_files = sorted(navwarn_dir.glob("NAVAREA_XX_*.json"))
    print(f"\n[20{year_suffix}] {len(json_files)} JSON files in {navwarn_dir}")

    updated = skipped = unmatched = 0

    for jpath in json_files:
        try:
            with open(jpath, encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  SKIP {jpath.name}: {exc}")
            skipped += 1
            continue

        nid = navxx_id_from_json(data)
        props = data.get("properties") or {}
        changed = False

        # --- valid_from ---
        if nid and nid in first_seen:
            new_from = f"{first_seen[nid]}T00:00:00+00:00"
            existing_until = props.get("valid_until")
            # Don't set valid_from later than an already-parsed valid_until
            if existing_until and new_from > existing_until:
                print(
                    f"  SKIP-FROM {jpath.name}: first_seen {first_seen[nid]}"
                    f" is after valid_until {existing_until[:10]}"
                )
            elif props.get("valid_from") != new_from:
                props["valid_from"] = new_from
                changed = True
        else:
            print(f"  NO-HISTORY {jpath.name} (id={nid}) — valid_from unchanged")
            unmatched += 1

        # --- valid_until (last-seen heuristic, only when not already parsed) ---
        # Never clear an explicit self-cancel valid_until already in the JSON.
        if nid and props.get("valid_until") is None:
            ls = last_seen.get(nid)
            if ls and ls < final_date:
                new_until = f"{ls}T23:59:59+00:00"
                props["valid_until"] = new_until
                changed = True

        if changed:
            updated += 1
            detail = (
                f"from={first_seen.get(nid, '??')} "
                f"until={props.get('valid_until') or 'active'}"
            )
            if write:
                with open(jpath, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=2)
                    fh.write("\n")
                print(f"  UPDATED {jpath.name}: {detail}")
            else:
                print(f"  WOULD UPDATE {jpath.name}: {detail}")

    print(
        f"  Done: {updated} updated, {skipped} skipped,"
        f" {unmatched} no-history (of {len(json_files)})"
    )


def main() -> None:
    write = "--write" in sys.argv

    # --year 25 or --year 26 (two-digit); default = all available
    target_years: list[str] = []
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--year" and i < len(sys.argv) - 1:
            target_years.append(sys.argv[i + 1].lstrip("20"))

    first_seen, last_seen, final_date = scan_all_snapshots()
    print(
        f"Scanned snapshots across all years: {len(first_seen)} unique IDs,"
        f" final date {final_date}"
    )

    if not first_seen:
        print("No snapshots found. Nothing to do.")
        return

    if not target_years:
        # Derive years from available history/20XX/navwarns directories
        target_years = sorted(
            d.name[2:]
            for d in (ROOT / "history").iterdir()
            if d.is_dir()
            and re.match(r"20\d{2}$", d.name)
            and (d / "navwarns").glob("NAVAREA_XX_*.json")
        )

    for yr in target_years:
        fix_year(yr, first_seen, last_seen, final_date, write)

    if not write:
        print("\nRe-run with --write to apply changes.")


if __name__ == "__main__":
    main()


def scan_snapshots() -> Tuple[Dict[str, str], Dict[str, str], str]:
    """Scan all daily snapshots for presence of each NAVAREA XX ID.

    Reads both ``<date>/navwarns_raw.txt`` (dated subdirectories)
    and ``ROSATOM_<date>_PAGEN_*.html`` files — whichever exist.

    Returns (first_seen, last_seen, final_date) where keys are
    IDs like ``NAVAREA XX 103/25``.
    """
    first_seen: Dict[str, str] = {}
    last_seen: Dict[str, str] = {}
    all_dates: Set[str] = set()

    # 1) Dated subdirectories with navwarns_raw.txt
    for child in sorted(NAVAREAXX_DIR.iterdir()):
        if not child.is_dir():
            continue
        dm = re.match(r"\d{4}-\d{2}-\d{2}", child.name)
        if not dm:
            continue
        date_str = child.name
        raw = child / "navwarns_raw.txt"
        if not raw.exists():
            continue
        all_dates.add(date_str)
        text = raw.read_text(errors="replace")
        for m in NAVXX_RE.finditer(text):
            nid = f"NAVAREA XX {m.group(1)}"
            if nid not in first_seen or date_str < first_seen[nid]:
                first_seen[nid] = date_str
            if nid not in last_seen or date_str > last_seen[nid]:
                last_seen[nid] = date_str

    # 2) HTML page files (same info, but ensures no gaps)
    for html_file in sorted(NAVAREAXX_DIR.glob("ROSATOM_*.html")):
        dm = re.search(r"(\d{4}-\d{2}-\d{2})", html_file.name)
        if not dm:
            continue
        date_str = dm.group(1)
        all_dates.add(date_str)
        text = html_file.read_text(errors="replace")
        for m in NAVXX_RE.finditer(text):
            nid = f"NAVAREA XX {m.group(1)}"
            if nid not in first_seen or date_str < first_seen[nid]:
                first_seen[nid] = date_str
            if nid not in last_seen or date_str > last_seen[nid]:
                last_seen[nid] = date_str

    final_date = max(all_dates) if all_dates else ""
    return first_seen, last_seen, final_date


def navxx_id_from_json(data: dict) -> Optional[str]:
    """Extract the canonical NAVAREA XX ID from a JSON feature."""
    props = data.get("properties") or {}
    mid = props.get("msg_id")
    if mid:
        return mid
    pid = props.get("parent_id")
    if pid:
        return pid
    fid = data.get("id") or ""
    return re.sub(r"#grp\d+$", "", fid) or None


def main() -> None:
    """Scan snapshots and fix NAVAREA XX JSON dates."""
    write = "--write" in sys.argv

    first_seen, last_seen, final_date = scan_snapshots()
    print(
        f"Scanned snapshots: {len(first_seen)} unique"
        f" NAVAREA XX IDs, final date {final_date}"
    )

    if not first_seen:
        print("No snapshots found. Nothing to do.")
        return

    # Filter to year-25 only
    first_seen = {k: v for k, v in first_seen.items() if k.endswith("/25")}
    last_seen = {k: v for k, v in last_seen.items() if k.endswith("/25")}
    print(f"Year-25 IDs in snapshots: {len(first_seen)}")

    updated = 0
    skipped = 0
    unmatched = 0

    json_files = sorted(NAVWARN_JSON_DIR.glob("NAVAREA_XX_*.json"))
    print(f"JSON files to process: {len(json_files)}")

    for jpath in json_files:
        try:
            with open(jpath, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  SKIP {jpath.name}: {exc}")
            skipped += 1
            continue

        nid = navxx_id_from_json(data)
        if not nid or nid not in first_seen:
            print(f"  UNMATCHED {jpath.name} (id={nid})")
            unmatched += 1
            continue

        props = data.get("properties") or {}
        changed = False

        # Fix valid_from: use first-seen date
        new_from = f"{first_seen[nid]}T00:00:00+00:00"
        old_from = props.get("valid_from")
        if old_from != new_from:
            props["valid_from"] = new_from
            changed = True

        # Fix valid_until: use last-seen + end of day,
        # but only if the message disappeared before the
        # final snapshot date (otherwise it may still be active)
        ls = last_seen.get(nid)
        if ls and ls < final_date:
            new_until = f"{ls}T23:59:59+00:00"
        else:
            new_until = None

        old_until = props.get("valid_until")
        if old_until != new_until:
            props["valid_until"] = new_until
            changed = True

        if changed:
            updated += 1
            if write:
                with open(jpath, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                    f.write("\n")
            else:
                detail = (
                    f"from={first_seen[nid]}"
                    f" until={ls if ls and ls < final_date else 'active'}"
                )
                print(f"  WOULD UPDATE {jpath.name}: {detail}")

    print(
        f"\nDone: {updated} updated,"
        f" {skipped} skipped, {unmatched} unmatched"
        f" (of {len(json_files)} files)"
    )
    if not write and updated > 0:
        print("Re-run with --write to apply changes.")


if __name__ == "__main__":
    main()
