"""Recover 2026 PRIP and NAVAREA XX JSON files from git history.

Due to a hardcoded ``history/2025/*`` in the scrape workflow (fixed
Mar 13 2026), files written to ``current/navwarns/`` and
``current/prips/`` were committed to git but their copies in
``history/2026/`` were not.  This script walks git history, extracts
every version of every year-26 file that was committed to ``current/``,
and copies it to the appropriate ``history/2026/`` subdirectory.

The raw HTML snapshots from the CI runners are gone — only the
parsed JSON files are recoverable.

Usage::

    python scripts/recover_2026_from_git.py          # dry-run
    python scripts/recover_2026_from_git.py --write   # recover files
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Set

ROOT = Path(__file__).resolve().parents[1]
HISTORY_NAVWARNS = ROOT / "history" / "2026" / "navwarns"
HISTORY_PRIPS = ROOT / "history" / "2026" / "prips"


def git_log_events(
    pathspec: str,
) -> list[tuple[str, str, str, str]]:
    """Return (date, commit, action, filepath) for A/D events."""
    result = subprocess.run(
        [
            "git",
            "log",
            "--all",
            "--reverse",
            "--diff-filter=AD",
            "--name-status",
            "--format=DATE:%ai %H",
            "--",
            pathspec,
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    entries: list[tuple[str, str, str, str]] = []
    current_date = ""
    current_hash = ""
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("DATE:"):
            # Format: DATE:YYYY-MM-DD HH:MM:SS +ZZZZ <hash>
            parts = line.split()
            current_date = parts[0][5:]  # YYYY-MM-DD
            current_hash = parts[3] if len(parts) > 3 else ""
        elif line and "\t" in line:
            action, fpath = line.split("\t", 1)
            entries.append((current_date, current_hash, action, fpath))
    return entries


def extract_file_from_git(
    commit: str,
    filepath: str,
) -> bytes | None:
    """Extract file contents from a specific git commit."""
    result = subprocess.run(
        ["git", "show", f"{commit}:{filepath}"],
        capture_output=True,
        cwd=str(ROOT),
    )
    if result.returncode == 0:
        return result.stdout
    return None


def main() -> None:
    """Recover 2026 data from git history."""
    write = "--write" in sys.argv

    print("Scanning git history for year-26 files in current/...")

    nxx_events = git_log_events("current/navwarns/NAVAREA_XX_*_26*")
    prip_events = git_log_events("current/prips/PRIP_*_26*")

    def normalise_name(fname: str) -> str:
        """Strip the trailing 4-digit year if present.

        e.g. PRIP_WEST_5_26_2026.json -> PRIP_WEST_5_26.json
        """
        return re.sub(r"_\d{4}\.json$", ".json", fname)

    # For each file, track its last Add commit and whether it
    # was subsequently Deleted (meaning it needs recovery)
    def build_file_map(
        events: list[tuple[str, str, str, str]],
    ) -> Dict[str, Dict[str, str]]:
        fmap: Dict[str, Dict[str, str]] = {}
        for date, commit, action, fpath in events:
            fname = os.path.basename(fpath)
            if not fname.endswith(".json"):
                continue
            if action == "A":
                fmap[fname] = {
                    "add_commit": commit,
                    "add_date": date,
                    "state": "A",
                    "path": fpath,
                }
            elif action == "D" and fname in fmap:
                fmap[fname]["state"] = "D"
                fmap[fname]["del_date"] = date
        return fmap

    nxx_map = build_file_map(nxx_events)
    prip_map = build_file_map(prip_events)

    n_del_nxx = sum(1 for v in nxx_map.values() if v["state"] == "D")
    n_del_prip = sum(1 for v in prip_map.values() if v["state"] == "D")
    print(
        f"Found {len(nxx_map)} NAVAREA XX files" f" ({n_del_nxx} deleted from current/)"
    )
    print(f"Found {len(prip_map)} PRIP files" f" ({n_del_prip} deleted from current/)")

    existing_nxx: Set[str] = set()
    if HISTORY_NAVWARNS.exists():
        existing_nxx = {f.name for f in HISTORY_NAVWARNS.glob("NAVAREA_XX_*.json")}
    existing_prips: Set[str] = set()
    if HISTORY_PRIPS.exists():
        existing_prips = {f.name for f in HISTORY_PRIPS.glob("PRIP_*.json")}

    recovered = 0
    skipped = 0
    failed = 0

    def recover_files(
        fmap: Dict[str, Dict[str, str]],
        existing: Set[str],
        dest_dir: Path,
    ) -> tuple[int, int, int]:
        rec, skip, fail = 0, 0, 0
        for fname, info in sorted(fmap.items()):
            norm = normalise_name(fname)
            if norm in existing:
                skip += 1
                continue
            # Only recover files deleted from current/
            # (still-active files don't need to be in history)
            if info["state"] != "D":
                skip += 1
                continue

            content = extract_file_from_git(info["add_commit"], info["path"])
            if content is None:
                print(f"  FAILED {fname}" f" (commit {info['add_commit'][:10]})")
                fail += 1
                continue

            dest = dest_dir / norm
            if write:
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content)
            print(
                f"  RECOVER {norm}"
                f" (added {info['add_date']},"
                f" removed {info.get('del_date', '?')})"
            )
            rec += 1
        return rec, skip, fail

    r, s, f = recover_files(nxx_map, existing_nxx, HISTORY_NAVWARNS)
    recovered += r
    skipped += s
    failed += f

    r, s, f = recover_files(prip_map, existing_prips, HISTORY_PRIPS)
    recovered += r
    skipped += s
    failed += f

    print(f"\nDone: {recovered} recovered," f" {skipped} skipped, {failed} failed")
    if not write and recovered > 0:
        print("Re-run with --write to write files.")

    print("\n--- NOT recoverable ---")
    print(
        "Raw HTML snapshots (history/2026/PRIP/*.html,"
        " history/2026/NAVAREAXX/*.html)"
    )
    print(
        "were never committed to git (Jan 1 - Mar 12)."
        " They only existed on ephemeral CI runners."
    )


if __name__ == "__main__":
    main()
