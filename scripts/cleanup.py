import argparse
import datetime
import json
import logging
import re
import shutil
from pathlib import Path
from typing import List, Set


def identify_year(filename: str) -> str:
    """
    Extract year from filename.
    Matches:
      - *_2024.json -> 2024
      - *_24.json -> 2024
    """
    # Check for 4 digit year at end of filename (before .json)
    m4 = re.search(r"_(\d{4})\.json$", filename)
    if m4:
        return m4.group(1)

    # Check for 2 digit year at end of filename
    m2 = re.search(r"_(\d{2})\.json$", filename)
    if m2:
        return f"20{m2.group(1)}"

    # Check for 2 digit year in middle (like HYDROARC_123_24_...)
    # But usually it is at the end.

    return "unknown"


def move_to_history(filename: str, current_dir: Path, history_dir_base: Path):
    year = identify_year(filename)
    # Target structure: history/<year>/navwarns/
    # Or history/<year>/prips/ if it came from prips?
    # The user said "old (obsolete) messages are in the history dir".

    # If the file is a prip (starts with PRIP), maybe put in history/<year>/prips?
    # If standard navwarn, history/<year>/navwarns?

    if filename.startswith("PRIP"):
        dest_subdir = "prips"
    else:
        dest_subdir = "navwarns"

    dest_dir = history_dir_base / year / dest_subdir
    dest_dir.mkdir(parents=True, exist_ok=True)

    src = current_dir / filename
    dst = dest_dir / filename

    logging.info(f"Moving obsolete message {filename} to {dest_dir}")
    try:
        shutil.move(str(src), str(dst))
    except Exception as e:
        logging.error(f"Failed to move {src} to {dst}: {e}")


def cleanup(
    active_files: Set[str],
    current_dir: Path,
    file_pattern: str,
    history_dir_base: Path = Path("history"),
) -> None:
    """Move files not in active_files to history."""
    if not current_dir.exists():
        return

    logging.info(f"Cleaning up {current_dir} matching {file_pattern}...")

    for file_path in current_dir.glob(file_pattern):
        filename = file_path.name
        if filename not in active_files:
            move_to_history(filename, current_dir, history_dir_base)


def _read_first_props(path: Path) -> dict:
    """Return properties dict from a Feature or first feature of a Collection."""
    try:
        d = json.loads(path.read_text())
    except Exception:
        return {}
    if d.get("type") == "Feature":
        return d.get("properties") or {}
    if d.get("type") == "FeatureCollection":
        feats = d.get("features") or []
        return feats[0].get("properties") or {} if feats else {}
    return {}


def remove_stale_no_expiry(
    current_dirs: List[Path],
    max_age_days: int = 90,
    history_dir_base: Path = Path("history"),
    dry_run: bool = False,
) -> List[Path]:
    """Move feature files with no valid_until whose valid_from exceeds max_age_days.

    Returns list of paths that were moved (or would be moved in dry-run).
    """
    cutoff = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(
        days=max_age_days
    )
    moved: List[Path] = []

    for current_dir in current_dirs:
        if not current_dir.exists():
            continue
        for file_path in sorted(current_dir.glob("*.json")):
            props = _read_first_props(file_path)
            if props.get("valid_until") is not None:
                continue  # has explicit expiry; not stale
            valid_from = props.get("valid_from")
            if not valid_from:
                continue  # no date info; skip conservatively
            try:
                vf_dt = datetime.datetime.fromisoformat(
                    valid_from.replace("Z", "+00:00")
                )
                if vf_dt.tzinfo is None:
                    vf_dt = vf_dt.replace(tzinfo=datetime.timezone.utc)
            except ValueError:
                continue
            if vf_dt < cutoff:
                logging.info(
                    "Stale no-expiry: %s (valid_from=%s)",
                    file_path.name,
                    valid_from,
                )
                if not dry_run:
                    move_to_history(file_path.name, current_dir, history_dir_base)
                moved.append(file_path)

    return moved


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        description="Remove stale no-expiry warning files from current/."
    )
    parser.add_argument(
        "--stale",
        type=int,
        default=90,
        metavar="DAYS",
        help="Move files with no valid_until older than DAYS days (default: 90)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be moved without moving anything",
    )
    parser.add_argument(
        "--history-dir",
        default="history",
        metavar="DIR",
        help="Base history directory (default: history)",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    dirs = [
        repo_root / "current" / "navwarns",
        repo_root / "current" / "prips",
    ]
    moved = remove_stale_no_expiry(
        dirs,
        max_age_days=args.stale,
        history_dir_base=repo_root / args.history_dir,
        dry_run=args.dry_run,
    )
    action = "Would move" if args.dry_run else "Moved"
    print(f"{action} {len(moved)} stale file(s).")
