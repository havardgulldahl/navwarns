"""Fix valid_from / valid_until for 2025 NAVAREA XX messages.

The early Rosatom scraper (started Sep 18 2025) did not correctly set
issue or expiry dates.  This script scans the daily raw-text snapshots
in ``history/2025/NAVAREAXX/<date>/navwarns_raw.txt`` to determine
when each NAVAREA XX ID first appeared and when it was last seen, then
patches the JSON files in ``history/2025/navwarns/`` accordingly.

Usage::

    python scripts/fix_navareaxx_dates_2025.py          # dry-run
    python scripts/fix_navareaxx_dates_2025.py --write   # apply changes
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
NAVAREAXX_DIR = ROOT / "history" / "2025" / "NAVAREAXX"
NAVWARN_JSON_DIR = ROOT / "history" / "2025" / "navwarns"

# Regex for NAVAREA XX <num>/<yr> in raw text or HTML
NAVXX_RE = re.compile(r"NAVAREA XX (\d+/\d+)")


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
