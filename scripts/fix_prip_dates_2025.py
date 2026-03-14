"""Fix valid_from / valid_until for 2025 PRIPs using HTML snapshots.

The early PRIP scraper (started Sep 23 2025) did not correctly set
issue or expiry dates.  This script scans the daily HTML snapshots
in ``history/2025/PRIP/`` to determine when each PRIP ID first
appeared (first_seen) and when it was last seen (last_seen), then
patches the JSON files in ``history/2025/prips/`` accordingly.

Usage::

    python scripts/fix_prip_dates_2025.py          # dry-run
    python scripts/fix_prip_dates_2025.py --write   # apply changes
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
PRIP_HTML_DIR = ROOT / "history" / "2025" / "PRIP"
PRIP_JSON_DIR = ROOT / "history" / "2025" / "prips"

RU_TO_EN: Dict[str, str] = {
    "АРХАНГЕЛЬСК": "ARKHANGELSK",
    "МУРМАНСК": "MURMANSK",
    "ЗАПАД": "WEST",
}

# Regex to find ПРИП <REGION> <num>/<yr> in HTML text
PRIP_RE = re.compile(r"ПРИП\s+(АРХАНГЕЛЬСК|МУРМАНСК|ЗАПАД)\s+0*(\d+)/(\d+)")


def scan_html_snapshots() -> Tuple[
    Dict[str, str],
    Dict[str, str],
    str,
]:
    """Scan all PRIP HTML snapshots for presence of each PRIP ID.

    Returns (first_seen, last_seen, final_date) where keys are
    normalised IDs like ``PRIP ARKHANGELSK 74/25``.
    """
    first_seen: Dict[str, str] = {}
    last_seen: Dict[str, str] = {}
    all_dates: Set[str] = set()

    for html_file in sorted(PRIP_HTML_DIR.glob("*.html")):
        dm = re.search(r"(\d{4}-\d{2}-\d{2})", html_file.name)
        if not dm:
            continue
        date_str = dm.group(1)
        all_dates.add(date_str)

        text = html_file.read_text(errors="replace")
        seen_ids: Set[str] = set()
        for m in PRIP_RE.finditer(text):
            region = RU_TO_EN[m.group(1)]
            num = m.group(2)
            yr = m.group(3)
            prip_id = f"PRIP {region} {num}/{yr}"
            seen_ids.add(prip_id)

        for pid in seen_ids:
            if pid not in first_seen or date_str < first_seen[pid]:
                first_seen[pid] = date_str
            if pid not in last_seen or date_str > last_seen[pid]:
                last_seen[pid] = date_str

    final_date = max(all_dates) if all_dates else ""
    return first_seen, last_seen, final_date


def prip_id_from_json(
    data: dict,
) -> Optional[str]:
    """Extract the canonical PRIP ID from a JSON feature."""
    props = data.get("properties") or {}
    # Direct msg_id
    mid = props.get("msg_id")
    if mid:
        return mid
    # parent_id for group features
    pid = props.get("parent_id")
    if pid:
        return pid
    # Fall back to feature id, stripping group suffix
    fid = data.get("id") or ""
    return re.sub(r"#grp\d+$", "", fid) or None


def main() -> None:
    """Scan HTML snapshots and fix PRIP JSON dates."""
    write = "--write" in sys.argv

    first_seen, last_seen, final_date = scan_html_snapshots()
    print(
        f"Scanned HTML snapshots: {len(first_seen)} unique"
        f" PRIP IDs, final date {final_date}"
    )

    if not first_seen:
        print("No HTML snapshots found. Nothing to do.")
        return

    # Filter to year-25 only (matching the JSON files)
    first_seen = {k: v for k, v in first_seen.items() if k.endswith("/25")}
    last_seen = {k: v for k, v in last_seen.items() if k.endswith("/25")}
    print(f"Year-25 PRIPs in snapshots: {len(first_seen)}")

    updated = 0
    skipped = 0
    unmatched = 0

    json_files = sorted(PRIP_JSON_DIR.glob("*.json"))
    print(f"JSON files to process: {len(json_files)}")

    for jpath in json_files:
        try:
            with open(jpath, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  SKIP {jpath.name}: {exc}")
            skipped += 1
            continue

        pid = prip_id_from_json(data)
        if not pid or pid not in first_seen:
            print(f"  UNMATCHED {jpath.name} (id={pid})")
            unmatched += 1
            continue

        props = data.get("properties") or {}
        changed = False

        # Fix valid_from: use first-seen date
        new_from = f"{first_seen[pid]}T00:00:00+00:00"
        old_from = props.get("valid_from")
        if old_from != new_from:
            props["valid_from"] = new_from
            changed = True

        # Fix valid_until: use last-seen + end of day,
        # but only if the PRIP disappeared before the final
        # snapshot date (otherwise it may still be active)
        ls = last_seen.get(pid)
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
                action = "WOULD UPDATE"
                detail = (
                    f"from={first_seen[pid]}"
                    f" until={ls if ls and ls < final_date else 'active'}"
                )
                print(f"  {action} {jpath.name}: {detail}")

    print(
        f"\nDone: {updated} updated,"
        f" {skipped} skipped, {unmatched} unmatched"
        f" (of {len(json_files)} files)"
    )
    if not write and updated > 0:
        print("Re-run with --write to apply changes.")


if __name__ == "__main__":
    main()
